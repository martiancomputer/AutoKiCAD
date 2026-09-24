"""IPC layer tests.

Split by dependency: socket resolution, degradation, and delegation need nothing
installed. Envelope round-trips need `python-protobuf` and generated bindings,
so they skip when absent. Nothing here needs a running KiCad or pynng --
`LoopbackTransport` stands in for the server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autokicad.backend import CliBackend
from autokicad.ipc import (
    IpcBackend,
    IpcUnavailable,
    LoopbackTransport,
    Transport,
    best_backend,
    default_socket_url,
    discover_socket_urls,
    probe,
)
from autokicad.ipc import codegen, protos
from .conftest import load

requires_protos = pytest.mark.skipif(
    not protos.available(),
    reason="needs python-protobuf and generated bindings "
    "(pacman -S python-protobuf; python -m autokicad.ipc.codegen)",
)


# --------------------------------------------------------------------------
# Socket resolution -- must match api_server.cpp / api_plugin_manager.cpp


def test_default_socket_matches_kicad_default(monkeypatch):
    monkeypatch.delenv("KICAD_API_SOCKET", raising=False)
    assert default_socket_url() == "ipc:///tmp/kicad/api.sock"


def test_env_socket_wins(monkeypatch):
    """KiCad passes KICAD_API_SOCKET to plugins it launches."""
    monkeypatch.setenv("KICAD_API_SOCKET", "/run/user/1000/kicad/api.sock")
    assert default_socket_url() == "ipc:///run/user/1000/kicad/api.sock"


def test_env_socket_with_scheme_is_untouched(monkeypatch):
    monkeypatch.setenv("KICAD_API_SOCKET", "ipc:///tmp/custom.sock")
    assert default_socket_url() == "ipc:///tmp/custom.sock"


def test_discover_includes_pid_sockets(monkeypatch, tmp_path):
    """KiCad falls back to api-<pid>.sock when api.sock is taken, so multiple
    instances mean multiple sockets."""
    monkeypatch.delenv("KICAD_API_SOCKET", raising=False)
    monkeypatch.setattr("autokicad.ipc.transport.DEFAULT_SOCKET_DIR", tmp_path)
    (tmp_path / "api.sock").touch()
    (tmp_path / "api-4242.sock").touch()

    urls = discover_socket_urls()
    assert urls[0].endswith("/api.sock")
    assert any("api-4242.sock" in u for u in urls)


def test_discover_deduplicates(monkeypatch, tmp_path):
    sock = tmp_path / "api.sock"
    sock.touch()
    monkeypatch.setenv("KICAD_API_SOCKET", str(sock))
    monkeypatch.setattr("autokicad.ipc.transport.DEFAULT_SOCKET_DIR", tmp_path)
    assert len(discover_socket_urls()) == len(set(discover_socket_urls()))


def test_discover_empty_when_no_sockets(monkeypatch, tmp_path):
    monkeypatch.delenv("KICAD_API_SOCKET", raising=False)
    monkeypatch.setattr("autokicad.ipc.transport.DEFAULT_SOCKET_DIR", tmp_path)
    assert discover_socket_urls() == []


# --------------------------------------------------------------------------
# Degradation: missing deps must explain themselves, never crash obscurely


def test_probe_returns_none_without_server(monkeypatch):
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/definitely-not-a-kicad.sock")
    assert probe() is None


def test_backend_unavailable_without_server(tmp_path, monkeypatch):
    """Point at a socket that definitively does not exist.

    This used to construct a bare IpcBackend, which picks up the ambient
    KICAD_API_SOCKET (default /tmp/kicad/api.sock). That made the test fail
    whenever a KiCad GUI happened to be running -- which it now is, during live
    schematic work -- reporting a regression that was really just a busy machine.
    """
    monkeypatch.setenv("KICAD_API_SOCKET", str(tmp_path / "definitely-not-here.sock"))

    be = IpcBackend()
    assert be.is_available() is False
    be.close()


def test_availability_check_fails_fast(monkeypatch):
    """Regression: pynng's `dial=` kwarg dials non-blocking, so construction
    succeeded against a dead socket and the failure only appeared as a timeout
    on first send -- `is_available()` blocked for the full 30s operation
    timeout. It must fail in well under a second."""
    pytest.importorskip("pynng")
    import time

    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/autokicad-definitely-absent.sock")
    be = IpcBackend(timeout_ms=30_000)
    start = time.monotonic()
    assert be.is_available() is False
    elapsed = time.monotonic() - start
    be.close()
    assert elapsed < 2.0, f"liveness check took {elapsed:.1f}s; must fail fast"


def test_protos_unavailable_message_is_actionable():
    if protos.available():
        pytest.skip("bindings present; degradation path not exercised")
    with pytest.raises(protos.ProtosUnavailable) as exc:
        protos.envelope()
    msg = str(exc.value)
    assert "pacman" in msg or "codegen" in msg


def test_codegen_check_passes_after_generation():
    """Bindings are committed-by-generation; --check guards staleness."""
    assert codegen.main(["--check"]) == 0


def test_codegen_finds_protos():
    assert codegen.proto_files(), "api/proto/**.proto should be present"
    assert any(p.name == "envelope.proto" for p in codegen.proto_files())


# --------------------------------------------------------------------------
# The honest limitation: no DRC over IPC


def test_drc_without_server_or_cli_raises(monkeypatch):
    """With nothing to talk to and no fallback, the error must name the cause."""
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/autokicad-absent.sock")
    be = IpcBackend(cli=None)
    with pytest.raises(IpcUnavailable):
        be.drc("board.kicad_pcb")


def test_drc_falls_back_to_cli_when_server_unreachable(monkeypatch, tmp_path):
    """`IpcBackend(cli=...)` is a preference for IPC, not a requirement. With no
    server reachable it must still produce an observation via the CLI."""
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/autokicad-absent.sock")
    calls = []

    class FakeCli:
        def drc(self, board, **kw):
            calls.append(Path(board))
            return CliBackend._parse_drc(load("drc_clean"), fallback_source=str(board))

        def observe(self, board, **kw):
            return self.drc(board, **kw)

    be = IpcBackend(cli=FakeCli())
    obs = be.drc(tmp_path / "b.kicad_pcb")
    assert calls == [tmp_path / "b.kicad_pcb"]
    assert obs.is_clean


def test_observe_falls_back_to_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/autokicad-absent.sock")

    class FakeCli:
        def drc(self, board, **kw):
            return CliBackend._parse_drc(load("drc_clean"), fallback_source=str(board))

        def observe(self, board, **kw):
            return self.drc(board, **kw)

    obs = IpcBackend(cli=FakeCli()).observe(tmp_path / "b.kicad_pcb")
    assert obs.is_clean


def test_ipc_backend_satisfies_backend_protocol():
    from autokicad.backend import Backend

    assert isinstance(IpcBackend(), Backend)


def test_best_backend_falls_back_to_cli(monkeypatch):
    """With no server live, best_backend must hand back the CLI, not raise."""
    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/nope.sock")
    monkeypatch.setattr(
        "autokicad.ipc.backend.CliBackend.__post_init__", lambda self: None
    )
    assert isinstance(best_backend(), CliBackend)


def test_best_backend_raises_when_nothing_available(monkeypatch):
    from autokicad.backend import KicadCliError

    monkeypatch.setenv("KICAD_API_SOCKET", "/tmp/nope.sock")

    def no_cli(self):
        raise KicadCliError("no kicad-cli")

    monkeypatch.setattr("autokicad.ipc.backend.CliBackend.__post_init__", no_cli)
    with pytest.raises(IpcUnavailable, match="neither"):
        best_backend()


# --------------------------------------------------------------------------
# Transport


def test_loopback_satisfies_transport_protocol():
    assert isinstance(LoopbackTransport(lambda b: b""), Transport)


def test_loopback_records_and_replies():
    t = LoopbackTransport(lambda payload: b"reply")
    assert t.send_recv(b"request") == b"reply"
    assert t.sent == [b"request"]
    t.close()
    assert t.closed


def test_pynng_transport_error_is_actionable():
    """pynng is not packaged for Arch; the error must say how to get it."""
    from autokicad.ipc.transport import PynngTransport, TransportError

    try:
        import pynng  # noqa: F401
    except ImportError:
        with pytest.raises(TransportError) as exc:
            PynngTransport("ipc:///tmp/nope.sock")
        assert "venv" in str(exc.value) or "pip install" in str(exc.value)
    else:
        pytest.skip("pynng installed; missing-dependency path not exercised")


# --------------------------------------------------------------------------
# Envelope round-trip -- needs the protobuf runtime


@requires_protos
def test_envelope_round_trip():
    env = protos.envelope()
    cmds = protos.base_commands()

    req = env.ApiRequest()
    req.header.kicad_token = "tok"
    req.header.client_name = "autokicad"
    req.message.Pack(cmds.Ping())

    restored = env.ApiRequest()
    restored.ParseFromString(req.SerializeToString())
    assert restored.header.kicad_token == "tok"
    assert restored.message.Is(cmds.Ping.DESCRIPTOR)


@requires_protos
def test_any_type_url_matches_kicad_dispatch():
    """KiCad matches ParseAnyTypeUrl(type_url) against RequestType().GetTypeName(),
    so the packed URL must end in the fully-qualified proto name."""
    cmds = protos.base_commands()
    env = protos.envelope()
    req = env.ApiRequest()
    req.message.Pack(cmds.Ping())
    assert req.message.type_url.endswith("kiapi.common.commands.Ping")


@requires_protos
def test_client_raises_on_error_status():
    from autokicad.ipc.client import ApiClient, ApiError

    env = protos.envelope()
    cmds = protos.base_commands()

    def server(_payload: bytes) -> bytes:
        resp = env.ApiResponse()
        resp.status.status = 3  # AS_BAD_REQUEST
        resp.status.error_message = "nope"
        return resp.SerializeToString()

    client = ApiClient(transport=LoopbackTransport(server), retries=1)
    with pytest.raises(ApiError, match="AS_BAD_REQUEST"):
        client.call(cmds.Ping(), cmds.GetVersionResponse)


@requires_protos
def test_client_retries_on_busy():
    from autokicad.ipc.client import ApiBusy, ApiClient

    env = protos.envelope()
    cmds = protos.base_commands()
    attempts = []

    def server(_payload: bytes) -> bytes:
        attempts.append(1)
        resp = env.ApiResponse()
        resp.status.status = 7  # AS_BUSY
        return resp.SerializeToString()

    client = ApiClient(
        transport=LoopbackTransport(server), retries=3, retry_delay_s=0.0
    )
    with pytest.raises(ApiBusy):
        client.call(cmds.Ping(), cmds.GetVersionResponse)
    assert len(attempts) == 3, "AS_BUSY is transient and must be retried"


@requires_protos
def test_client_does_not_retry_on_bad_request():
    from autokicad.ipc.client import ApiClient, ApiError

    env = protos.envelope()
    cmds = protos.base_commands()
    attempts = []

    def server(_payload: bytes) -> bytes:
        attempts.append(1)
        resp = env.ApiResponse()
        resp.status.status = 3
        return resp.SerializeToString()

    client = ApiClient(
        transport=LoopbackTransport(server), retries=3, retry_delay_s=0.0
    )
    with pytest.raises(ApiError):
        client.call(cmds.Ping(), cmds.GetVersionResponse)
    assert len(attempts) == 1, "permanent failures must not be retried"


@requires_protos
def test_client_maps_token_mismatch():
    from autokicad.ipc.client import ApiClient, TokenMismatch

    env = protos.envelope()
    cmds = protos.base_commands()

    def server(_payload: bytes) -> bytes:
        resp = env.ApiResponse()
        resp.status.status = 6  # AS_TOKEN_MISMATCH
        return resp.SerializeToString()

    client = ApiClient(transport=LoopbackTransport(server), retries=1)
    with pytest.raises(TokenMismatch):
        client.call(cmds.Ping(), cmds.GetVersionResponse)


@requires_protos
def test_client_sends_token_from_env(monkeypatch):
    monkeypatch.setenv("KICAD_API_TOKEN", "secret-token")
    env = protos.envelope()
    cmds = protos.base_commands()
    seen = {}

    def server(payload: bytes) -> bytes:
        req = env.ApiRequest()
        req.ParseFromString(payload)
        seen["token"] = req.header.kicad_token
        resp = env.ApiResponse()
        resp.status.status = 1  # AS_OK
        resp.message.Pack(cmds.GetVersionResponse())
        return resp.SerializeToString()

    from autokicad.ipc.client import ApiClient

    ApiClient(transport=LoopbackTransport(server)).call(
        cmds.Ping(), cmds.GetVersionResponse
    )
    assert seen["token"] == "secret-token"
