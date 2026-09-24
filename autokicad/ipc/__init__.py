"""KiCad IPC API client (protobuf over nng).

    from autokicad.ipc import IpcBackend, probe

    if probe():                       # is a KiCad API server live?
        with IpcBackend(cli=CliBackend()) as be:
            print(be.version())

Requires, in this order:
  1. `python -m autokicad.ipc.codegen`   -- generate bindings from api/proto/**
  2. `sudo pacman -S python-protobuf`    -- runtime, must match protoc major
  3. `pip install pynng` (in a venv)     -- nng transport, not packaged for Arch
  4. A running server: KiCad with the API enabled, or `kicad-cli api-server`
     (master only -- released 10.x has no such subcommand).
"""

from .backend import IpcBackend, IpcUnavailable, best_backend
from .client import (
    ApiBusy,
    ApiClient,
    ApiError,
    ApiUnimplemented,
    TokenMismatch,
    probe,
)
from .protos import ProtosUnavailable
from .transport import (
    LoopbackTransport,
    PynngTransport,
    Transport,
    TransportError,
    default_socket_url,
    discover_socket_urls,
)

__all__ = [
    "ApiBusy",
    "ApiClient",
    "ApiError",
    "ApiUnimplemented",
    "IpcBackend",
    "IpcUnavailable",
    "LoopbackTransport",
    "ProtosUnavailable",
    "PynngTransport",
    "TokenMismatch",
    "Transport",
    "TransportError",
    "best_backend",
    "default_socket_url",
    "discover_socket_urls",
    "probe",
]
