"""IPC backend -- same `Backend` protocol, different transport.

DRC runs over IPC via the `RunDrc` command we added to
`api/proto/board/board_commands.proto`, handled by `API_HANDLER_PCB::handleRunDrc`.
Verified to produce observations identical to `CliBackend` -- same counts, types,
severities and coordinates -- on the same board.

The reason to prefer it: **IPC sees the board as it currently is in the editor,
unsaved edits included.** The CLI structurally cannot; it only ever reads the
last saved file from disk. For an agent mutating a board and asking what it
broke, that difference is the whole point.

`RunDrc` is master-only. Pointed at an older server the call returns
AS_UNHANDLED, and `drc()` falls back to the CLI when one is configured.

Visuals still go through the CLI, since rendering needs a file on disk.
`RunBoardJobExportSvg` / `RunBoardJobExportRender` exist in the API, so that is
the next migration candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..backend import Backend, CliBackend, KicadCliError
from ..models import (
    AffectedItem,
    Category,
    IgnoredCheck,
    Observation,
    Position,
    Severity,
    Violation,
)
from . import protos
from .client import ApiClient, ApiError, ApiUnimplemented
from .transport import Transport, TransportError, discover_socket_urls


class IpcUnavailable(RuntimeError):
    """No API server reachable, or the bindings/runtime are missing."""


@dataclass
class IpcBackend:
    """Talks to a live KiCad over IPC, with CLI fallback for DRC.

    Args:
        transport: pre-built transport; one is created on demand if omitted.
        cli: fallback used for DRC. Set to None to forbid falling back, in which
            case `observe()` raises rather than silently changing transport.
        client_name: shows up in KiCad's API logs; keep it identifiable.
    """

    transport: Transport | None = None
    cli: CliBackend | None = None
    client_name: str = "autokicad"
    timeout_ms: int = 30_000
    _client: ApiClient | None = field(default=None, init=False, repr=False)

    # ---- connection ------------------------------------------------------

    @property
    def client(self) -> ApiClient:
        if self._client is None:
            try:
                self._client = ApiClient(
                    transport=self.transport,
                    client_name=self.client_name,
                    timeout_ms=self.timeout_ms,
                )
            except (TransportError, protos.ProtosUnavailable) as exc:
                raise IpcUnavailable(str(exc)) from exc
        return self._client

    def is_available(self) -> bool:
        try:
            self.client.ping()
            return True
        except (IpcUnavailable, ApiError, TransportError):
            return False

    def version(self) -> str:
        return self.client.version()

    @staticmethod
    def candidate_sockets() -> list[str]:
        """Sockets that look like a KiCad API server, for diagnostics."""
        return discover_socket_urls()

    # ---- live state that the CLI cannot reach -----------------------------

    def open_boards(self) -> list:
        """Boards currently open in the editor.

        The whole point of the IPC path: reflects unsaved in-editor state.
        """
        types = protos.base_types()
        return self.client.open_documents(types.DocumentType.DOCTYPE_PCB)

    def nets(self, document) -> list:
        return self.client.nets(document)

    # ---- observe ---------------------------------------------------------

    def supports_drc(self) -> bool:
        """True when the connected KiCad exposes the RunDrc command.

        RunDrc landed on master after 10.0; a client built against these protos
        can still be pointed at an older server, which answers AS_UNHANDLED.
        """
        try:
            return hasattr(protos.board_commands(), "RunDrc")
        except protos.ProtosUnavailable:
            return False

    def drc(self, board: Path | str | None = None, **kwargs) -> Observation:
        """Run DRC on the open board, over IPC.

        Unlike the CLI path this sees the board **as it currently is in the
        editor**, including unsaved edits -- the CLI can only ever read the last
        saved file. `board` is accepted for signature compatibility and used
        only to label the result; the server always checks its open document.

        Falls back to the CLI if the server is too old to know RunDrc.
        """
        try:
            cmds = protos.board_commands()
            doc = self._document()
        except (protos.ProtosUnavailable, IpcUnavailable, TransportError) as exc:
            # No reachable server, or no bindings. Degrade to the CLI when one is
            # configured rather than failing: callers asked for an observation,
            # not for a particular transport.
            if self.cli is not None:
                return self.cli.drc(board, **kwargs)
            raise IpcUnavailable(str(exc)) from exc

        req = cmds.RunDrc()
        req.board.CopyFrom(doc)
        req.units = protos.enums().Units.U_MM
        req.report_all_track_errors = bool(kwargs.get("all_track_errors", False))
        req.check_schematic_parity = bool(kwargs.get("schematic_parity", False))

        # proto3 scalars have no presence, so the server needs telling that a
        # false here is deliberate rather than simply absent.
        if "refill_zones" in kwargs:
            req.refill_zones = bool(kwargs["refill_zones"])
            req.refill_zones_set = True

        try:
            resp = self.client.call(req, cmds.RunDrcResponse)
        except ApiUnimplemented:
            if self.cli is None:
                raise
            return self.cli.drc(board, **kwargs)

        source = str(board) if board else getattr(doc, "board_filename", "") or "<open board>"
        return self._to_observation(resp, source)

    def _document(self):
        docs = self.open_boards()
        if not docs:
            raise IpcUnavailable("no board is open in the connected KiCad")
        return docs[0]

    @staticmethod
    def _to_observation(resp, source: str) -> Observation:
        """Map RunDrcResponse onto the same model CliBackend produces.

        Coordinates arrive as int64 nanometres (common.types.Vector2); the CLI
        path reports them in the requested display units. Normalise to mm here
        so an Observation means the same thing whichever backend produced it.
        """
        # RuleSeverity -> our Severity. Values from common/types/base_types.proto.
        sev_map = {
            1: Severity.WARNING,
            2: Severity.ERROR,
            3: Severity.EXCLUSION,
            5: Severity.INFO,
        }

        def convert(pb, category: Category) -> Violation:
            return Violation(
                type=pb.type,
                description=pb.description,
                severity=sev_map.get(pb.severity, Severity.UNKNOWN),
                category=category,
                items=tuple(
                    AffectedItem(
                        uuid=i.id.value,
                        description=i.description,
                        pos=Position(i.position.x_nm / 1e6, i.position.y_nm / 1e6),
                    )
                    for i in pb.items
                ),
                excluded=pb.excluded,
                comment=pb.exclusion_comment or None,
            )

        violations: list[Violation] = []
        for entries, category in (
            (resp.violations, Category.VIOLATION),
            (resp.unconnected_items, Category.UNCONNECTED),
            (resp.schematic_parity, Category.PARITY),
        ):
            violations.extend(convert(v, category) for v in entries)

        sev_names = {1: "warning", 2: "error", 3: "exclusion", 5: "info"}

        return Observation(
            source=source,
            coordinate_units="mm",
            violations=violations,
            ignored_checks=[
                IgnoredCheck(c.key, c.description) for c in resp.ignored_checks
            ],
            included_severities=[
                sev_names.get(s, str(s)) for s in resp.included_severities
            ],
        )

    def observe(self, board: Path | str | None = None, **kwargs) -> Observation:
        """Same contract as `CliBackend.observe`, with DRC over IPC.

        Visuals still go through the CLI, since generating them needs a file on
        disk. `RunBoardJobExportSvg` / `RunBoardJobExportRender` exist in the
        API, so that is the next migration candidate.
        """
        render = kwargs.pop("render", False)
        layers = kwargs.pop("layers", None)
        artifacts = kwargs.pop("artifacts", None)

        obs = self.drc(board, **kwargs)

        if (render or layers) and board is not None and self.cli is not None:
            visual = self.cli.observe(
                board, render=render, layers=layers, artifacts=artifacts,
                refill_zones=False,
            )
            obs.render_paths.extend(visual.render_paths)
        elif render or layers:
            obs.render_paths.append(
                "<visuals need a CliBackend and a board path on disk>"
            )

        return obs

    # ---- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> IpcBackend:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def best_backend(*, prefer_ipc: bool = True, **cli_kwargs) -> Backend:
    """Pick IPC when a server is live, otherwise the CLI.

    Both satisfy `Backend`, so callers do not branch. Note that IPC only buys
    you anything once the DRC handler exists or you need live editor state --
    today the CLI is the honest default.
    """
    cli: CliBackend | None
    try:
        cli = CliBackend(**cli_kwargs)
    except KicadCliError:
        cli = None

    if prefer_ipc:
        candidate = IpcBackend(cli=cli)
        if candidate.is_available():
            return candidate
        candidate.close()

    if cli is None:
        raise IpcUnavailable(
            "neither a KiCad API server nor kicad-cli is available"
        )
    return cli
