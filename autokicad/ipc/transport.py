"""Wire transport for the KiCad IPC API.

KiCad's server is an **nng REP0** socket (`libs/kinng/src/kinng.cpp` calls
`nng_rep0_open`), listening on an IPC (Unix domain socket) URL -- by default
`ipc:///tmp/kicad/api.sock`, or `api-<pid>.sock` when that path is already taken.
A client must therefore speak nng **REQ0**.

nng's SP framing is a real binary protocol, not a bare Unix socket, so we do not
hand-roll it. `PynngTransport` delegates to `pynng`. The `Transport` protocol
exists so a future implementation (a raw REQ0 codec, or a bridge over the
kicad-cli process) can be swapped in without touching the client.

`pynng` is not packaged for Arch. Install it into a venv:

    python -m venv .venv && .venv/bin/pip install pynng
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_SOCKET_DIR = Path("/tmp/kicad")
DEFAULT_SOCKET_NAME = "api.sock"


class TransportError(RuntimeError):
    """The transport could not be established, or a send/recv failed."""


@runtime_checkable
class Transport(Protocol):
    """A synchronous request/reply channel carrying opaque bytes."""

    def send_recv(self, payload: bytes, *, timeout_ms: int | None = None) -> bytes: ...
    def close(self) -> None: ...


def default_socket_url() -> str:
    """Resolve the server URL the way a KiCad API plugin would.

    KiCad passes `KICAD_API_SOCKET` to plugins it launches
    (`common/api/api_plugin_manager.cpp`), so honour that first. Otherwise fall
    back to the documented default path.
    """
    from_env = os.environ.get("KICAD_API_SOCKET")
    if from_env:
        return from_env if "://" in from_env else f"ipc://{from_env}"
    return f"ipc://{DEFAULT_SOCKET_DIR / DEFAULT_SOCKET_NAME}"


def discover_socket_urls() -> list[str]:
    """Candidate sockets, best first.

    KiCad renames to `api-<pid>.sock` when `api.sock` already exists, so a box
    running several instances has several sockets. Useful for diagnostics; a
    caller that cares which KiCad it talks to should pass an explicit URL.
    """
    urls: list[str] = []
    if (env := os.environ.get("KICAD_API_SOCKET")):
        urls.append(env if "://" in env else f"ipc://{env}")

    default = DEFAULT_SOCKET_DIR / DEFAULT_SOCKET_NAME
    if default.exists():
        urls.append(f"ipc://{default}")
    if DEFAULT_SOCKET_DIR.is_dir():
        for p in sorted(DEFAULT_SOCKET_DIR.glob("api-*.sock")):
            urls.append(f"ipc://{p}")

    # Preserve order, drop duplicates.
    return list(dict.fromkeys(urls))


class PynngTransport:
    """nng REQ0 client over `pynng`.

    One socket per instance; not thread-safe, because REQ0 is a strict
    alternating request/reply state machine. Give each thread its own transport.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout_ms: int = 30_000,
        connect_timeout_ms: int = 2_000,
    ):
        """
        `connect_timeout_ms` is separate from `timeout_ms` on purpose. Passing
        `dial=` to pynng's constructor dials *non-blocking*: construction
        succeeds even when nothing is listening, and the failure only surfaces
        as a timeout on the first send. That made `is_available()` block for the
        full operation timeout against a dead socket -- unacceptable for an
        agent doing a liveness check. Dialling with `block=True` surfaces
        ECONNREFUSED immediately instead.
        """
        try:
            import pynng  # noqa: PLC0415  (optional dependency)
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise TransportError(
                "pynng is required for the IPC transport but is not installed.\n"
                "It is not in the Arch repositories; use a venv:\n"
                "    python -m venv .venv && .venv/bin/pip install pynng"
            ) from exc

        self._pynng = pynng
        self.url = url or default_socket_url()
        self.timeout_ms = timeout_ms
        self._sock = None
        try:
            self._sock = pynng.Req0(
                recv_timeout=timeout_ms, send_timeout=timeout_ms
            )
            # Blocking dial: fail now if no server is listening, not later.
            self._sock.dial(self.url, block=True)
        except Exception as exc:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
            raise TransportError(
                f"could not connect to KiCad API at {self.url}: {exc}\n"
                "Is a KiCad instance running with the API enabled, or "
                "`kicad-cli api-server` started? Note api-server exists only on "
                "KiCad master, not in released 10.x."
            ) from exc

    def send_recv(self, payload: bytes, *, timeout_ms: int | None = None) -> bytes:
        if timeout_ms is not None:
            self._sock.recv_timeout = timeout_ms
            self._sock.send_timeout = timeout_ms
        try:
            self._sock.send(payload)
            return self._sock.recv()
        except Exception as exc:
            raise TransportError(f"IPC exchange failed on {self.url}: {exc}") from exc
        finally:
            if timeout_ms is not None:
                self._sock.recv_timeout = self.timeout_ms
                self._sock.send_timeout = self.timeout_ms

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self) -> PynngTransport:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class LoopbackTransport:
    """In-process fake: a callable stands in for the server.

    Exists so the envelope layer and IpcBackend are testable without KiCad, and
    without pynng. The handler receives the raw request bytes and returns raw
    response bytes.
    """

    def __init__(self, handler):
        self.handler = handler
        self.sent: list[bytes] = []
        self.closed = False

    def send_recv(self, payload: bytes, *, timeout_ms: int | None = None) -> bytes:
        self.sent.append(payload)
        return self.handler(payload)

    def close(self) -> None:
        self.closed = True
