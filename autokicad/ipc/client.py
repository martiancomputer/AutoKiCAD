"""Envelope layer for the KiCad IPC API.

Every exchange is `ApiRequest` -> `ApiResponse` (`api/proto/common/envelope.proto`),
with the actual command wrapped in a `google.protobuf.Any`. KiCad dispatches on
the Any's type URL: `common/api/api_handler.cpp` calls
`Any::ParseAnyTypeUrl()` and matches the result against
`RequestType().GetTypeName()`. Python's stock `Any.Pack()` emits
`type.googleapis.com/kiapi.common.commands.Ping`, which parses to exactly that
name -- so no custom type-URL handling is needed.

This layer owns the token, the client name, Any packing, and turning
`ApiStatusCode` into exceptions. It does not know what any individual command
means.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import protos
from .transport import PynngTransport, Transport, TransportError

DEFAULT_CLIENT_NAME = "autokicad"


class ApiError(RuntimeError):
    """A non-OK ApiResponseStatus."""

    def __init__(self, status_name: str, message: str, code: int):
        super().__init__(f"{status_name}: {message}" if message else status_name)
        self.status_name = status_name
        self.code = code
        self.message = message


class ApiBusy(ApiError):
    """AS_BUSY or AS_NOT_READY -- transient, worth retrying."""


class ApiUnimplemented(ApiError):
    """AS_UNIMPLEMENTED -- the command exists in the schema but has no handler."""


class TokenMismatch(ApiError):
    """AS_TOKEN_MISMATCH -- wrong or missing KICAD_API_TOKEN."""


# Names mirror ApiStatusCode in envelope.proto. Kept as a literal map so a
# missing generated enum doesn't stop us reporting a useful error.
_STATUS_NAMES = {
    0: "AS_UNKNOWN",
    1: "AS_OK",
    2: "AS_TIMEOUT",
    3: "AS_BAD_REQUEST",
    4: "AS_NOT_READY",
    5: "AS_UNHANDLED",
    6: "AS_TOKEN_MISMATCH",
    7: "AS_BUSY",
    8: "AS_UNIMPLEMENTED",
}
_OK = 1
_TRANSIENT = {4, 7}  # AS_NOT_READY, AS_BUSY


@dataclass
class ApiClient:
    """Synchronous KiCad API client.

    `token` defaults to `KICAD_API_TOKEN`, which KiCad sets for plugins it
    launches. A standalone client talking to `kicad-cli api-server` may not need
    one; an empty token is sent as empty and KiCad will answer
    AS_TOKEN_MISMATCH if it does require it.
    """

    transport: Transport | None = None
    token: str = field(default_factory=lambda: os.environ.get("KICAD_API_TOKEN", ""))
    client_name: str = DEFAULT_CLIENT_NAME
    timeout_ms: int = 30_000
    retries: int = 3
    retry_delay_s: float = 0.5
    _owns_transport: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.transport is None:
            self.transport = PynngTransport(timeout_ms=self.timeout_ms)
            self._owns_transport = True

    # ---- core exchange ---------------------------------------------------

    def call(self, request_msg, response_type):
        """Send one command, return an instance of `response_type`.

        Retries only on AS_BUSY / AS_NOT_READY: KiCad reports those while it is
        mid-operation or still starting, and an agent driving a live editor will
        hit them routinely.
        """
        env = protos.envelope()

        req = env.ApiRequest()
        req.header.kicad_token = self.token
        req.header.client_name = self.client_name
        req.message.Pack(request_msg)
        payload = req.SerializeToString()

        last: ApiError | None = None
        for attempt in range(max(1, self.retries)):
            raw = self.transport.send_recv(payload, timeout_ms=self.timeout_ms)

            resp = env.ApiResponse()
            resp.ParseFromString(raw)
            code = int(resp.status.status)

            if code == _OK:
                out = response_type()
                # A few commands legitimately answer with an empty Any.
                if resp.message.value or resp.message.type_url:
                    if not resp.message.Unpack(out):
                        raise ApiError(
                            "AS_BAD_REQUEST",
                            f"response was {resp.message.type_url!r}, expected "
                            f"{response_type.DESCRIPTOR.full_name!r}",
                            3,
                        )
                return out

            last = self._to_error(code, resp.status.error_message)
            if code not in _TRANSIENT:
                raise last
            if attempt + 1 < max(1, self.retries):
                time.sleep(self.retry_delay_s * (attempt + 1))

        assert last is not None
        raise last

    @staticmethod
    def _to_error(code: int, message: str) -> ApiError:
        name = _STATUS_NAMES.get(code, f"AS_({code})")
        if code in _TRANSIENT:
            return ApiBusy(name, message, code)
        if code == 6:
            return TokenMismatch(name, message, code)
        if code == 8:
            return ApiUnimplemented(name, message, code)
        return ApiError(name, message, code)

    # ---- the handful of commands that exist today ------------------------
    #
    # Deliberately thin. There is no DRC or ERC command in api/proto at all, so
    # this cannot be a complete observe path -- see backend.IpcBackend.

    def ping(self) -> None:
        cmds = protos.base_commands()
        from google.protobuf import empty_pb2  # noqa: PLC0415

        self.call(cmds.Ping(), empty_pb2.Empty)

    def version(self) -> str:
        cmds = protos.base_commands()
        resp = self.call(cmds.GetVersion(), cmds.GetVersionResponse)
        v = resp.version
        return getattr(v, "full_version", "") or (
            f"{v.major}.{v.minor}.{v.patch}"
        )

    def kicad_binary_path(self, name: str) -> str:
        cmds = protos.base_commands()
        req = cmds.GetKiCadBinaryPath()
        req.binary_name = name
        return self.call(req, cmds.PathResponse).path

    def open_documents(self, doc_type: int) -> list:
        """List open documents of a given DocumentType."""
        cmds = protos.editor_commands()
        req = cmds.GetOpenDocuments()
        req.type = doc_type
        return list(self.call(req, cmds.GetOpenDocumentsResponse).documents)

    def nets(self, document) -> list:
        """All nets on a board. `document` is a common.types.DocumentSpecifier."""
        cmds = protos.board_commands()
        req = cmds.GetNets()
        req.board.CopyFrom(document)
        return list(self.call(req, cmds.NetsResponse).nets)

    def list_libraries(self, library_type: int, scope: int | None = None) -> list:
        """Libraries KiCad is configured to use, for one LibraryType.

        Needs no open document: it reads the library tables rather than loading
        anything from disk. Each entry reports both the configured `uri` and the
        `resolved_uri`, because an unresolved ${KICAD10_FOOTPRINT_DIR} looks fine in
        the former and points nowhere in the latter.
        """
        cmds = protos.base_commands()
        req = cmds.ListLibraries()
        req.type = library_type

        if scope is not None:
            req.scope = scope

        return list(self.call(req, cmds.ListLibrariesResponse).libraries)

    def search_footprints(
        self,
        document,
        query: str = "",
        libraries: list[str] | None = None,
        *,
        max_results: int = 0,
        match_all_terms: bool = False,
    ):
        """Search the configured footprint libraries, returning the raw response.

        `libraries` restricts the search by nickname; omitting it searches all of
        them, which on a cold fp-info-cache reads every configured library. Set
        `match_all_terms` to require each whitespace-separated term, which is what
        makes a query like "0603 resistor" work.
        """
        cmds = protos.board_commands()
        req = cmds.SearchFootprints()
        req.board.CopyFrom(document)
        req.query = query
        req.max_results = max_results
        req.match_all_terms = match_all_terms
        req.libraries.extend(libraries or [])

        return self.call(req, cmds.SearchFootprintsResponse)

    def autoplace_footprints(
        self,
        document,
        footprints: list[str] | None = None,
        *,
        place_offboard: bool = False,
        override_locks: bool = False,
    ):
        """Autoplace footprints on the open board, returning the raw response.

        `footprints` is a list of KIID strings; None means every footprint on the
        board. The response carries the result enum, the KIIDs that actually moved,
        the ones declined because they are locked, and total ratsnest length before
        and after -- the objective placement minimises, so it is how a caller tells
        an improvement from a no-op.

        The board is modified in place and nothing is written to disk.
        """
        cmds = protos.board_commands()
        req = cmds.AutoplaceFootprints()
        req.board.CopyFrom(document)
        req.place_offboard = place_offboard
        req.override_locks = override_locks

        for kiid in footprints or []:
            req.footprints.add().value = kiid

        return self.call(req, cmds.AutoplaceFootprintsResponse)

    # ---- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if self._owns_transport and self.transport is not None:
            self.transport.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def probe(url: str | None = None, *, timeout_ms: int = 2_000) -> str | None:
    """Return KiCad's version if an API server answers, else None.

    Cheap liveness check that never raises, for deciding whether to use the IPC
    path or fall back to the CLI.
    """
    try:
        transport = PynngTransport(url, timeout_ms=timeout_ms)
    except TransportError:
        return None
    try:
        with ApiClient(transport=transport, timeout_ms=timeout_ms, retries=1) as c:
            return c.version()
    except (ApiError, TransportError, protos.ProtosUnavailable):
        return None
    finally:
        transport.close()
