"""Backend tests: environment hygiene, argument construction, failure handling.

These use a stubbed subprocess so they run without KiCad. The genuinely
important ones are `test_clean_env_strips_appimage_vars` (a real failure we hit)
and `test_drc_never_writes_to_the_board` (guards against mutating the user's
board file).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from autokicad.backend import CliBackend, KicadCliError, clean_env
from autokicad.models import Category, Severity
from .conftest import load


# --------------------------------------------------------------------------
# Environment hygiene -- the AppImage bug


def test_clean_env_strips_appimage_vars(monkeypatch):
    """Claude Desktop's AppImage exports APPDIR, and KiCad honours it for path
    resolution -- which made kicad-cli look for _pcbnew.kiface inside the
    Claude mount and fail. Regression guard."""
    monkeypatch.setenv("APPDIR", "/tmp/.mount_claudeXXXX")
    monkeypatch.setenv("APPIMAGE", "/opt/claude/claude.AppImage")
    monkeypatch.setenv("OWD", "/home/someone")
    monkeypatch.setenv("ARGV0", "claude")

    env = clean_env()
    for var in ("APPDIR", "APPIMAGE", "OWD", "ARGV0"):
        assert var not in env, f"{var} must be stripped from the child environment"


def test_clean_env_preserves_everything_else(monkeypatch):
    monkeypatch.setenv("APPDIR", "/tmp/.mount_x")
    monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
    env = clean_env()
    assert env["KICAD9_FOOTPRINT_DIR"] == "/usr/share/kicad/footprints"
    assert "PATH" in env


def test_clean_env_does_not_mutate_process_env(monkeypatch):
    monkeypatch.setenv("APPDIR", "/tmp/.mount_x")
    clean_env()
    assert os.environ.get("APPDIR") == "/tmp/.mount_x"


def test_clean_env_accepts_explicit_base():
    env = clean_env({"APPDIR": "/x", "FOO": "bar"})
    assert env == {"FOO": "bar"}


# --------------------------------------------------------------------------
# Report parsing


def test_parse_assigns_categories():
    obs = CliBackend._parse_drc(load("drc_mixed"), fallback_source="f.kicad_pcb")
    assert obs.of(category=Category.UNCONNECTED)
    assert obs.of(category=Category.PARITY)
    assert obs.of(category=Category.VIOLATION)


def test_parse_reads_metadata():
    obs = CliBackend._parse_drc(load("drc_mixed"), fallback_source="fallback")
    assert obs.kicad_version
    assert obs.coordinate_units == "mm"
    assert obs.ignored_checks or obs.ignored_checks == []


def test_parse_falls_back_to_given_source():
    obs = CliBackend._parse_drc({}, fallback_source="only.kicad_pcb")
    assert obs.source == "only.kicad_pcb"


def test_parse_tolerates_empty_and_null_arrays():
    """A report with nulls or absent buckets must parse to an empty observation."""
    obs = CliBackend._parse_drc(
        {"source": "s", "violations": None, "unconnected_items": None},
        fallback_source="s",
    )
    assert obs.violations == []
    assert obs.is_clean


def test_parse_preserves_net_name_in_description():
    """The autorouter needs the net identity, which KiCad puts in the item text."""
    obs = CliBackend._parse_drc(load("drc_mixed"), fallback_source="s")
    unconnected = obs.of(category=Category.UNCONNECTED)
    assert any("[" in i.description for v in unconnected for i in v.items)


def test_parse_all_unconnected_are_error_severity():
    obs = CliBackend._parse_drc(load("drc_mixed"), fallback_source="s")
    assert all(
        v.severity is Severity.ERROR for v in obs.of(category=Category.UNCONNECTED)
    )


# --------------------------------------------------------------------------
# Argument construction, via a stubbed subprocess


class _Recorder:
    """Captures argv and writes a caller-supplied report to the -o path."""

    def __init__(self, report: dict | None, returncode: int = 0):
        self.report = report
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        # Only satisfy the DRC call. Render/plot invocations also use -o, and
        # writing JSON to their output path would make a failed render look
        # like a success -- which silently defeated this stub's first version.
        is_drc = "--format" in argv and "json" in argv
        if self.report is not None and is_drc and "-o" in argv:
            out = Path(argv[argv.index("-o") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(self.report), encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.returncode, "stdout", "stderr")

    @property
    def argv(self) -> list[str]:
        return self.calls[-1]


@pytest.fixture
def board(tmp_path) -> Path:
    p = tmp_path / "b.kicad_pcb"
    p.write_text("(kicad_pcb)", encoding="utf-8")
    return p


@pytest.fixture
def stub(monkeypatch):
    def install(report=load("drc_clean"), returncode=0):
        rec = _Recorder(report, returncode)
        monkeypatch.setattr(subprocess, "run", rec)
        return rec

    return install


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(
        "autokicad.backend.shutil.which", lambda *a, **k: "/usr/bin/kicad-cli"
    )
    return CliBackend()


def test_drc_never_writes_to_the_board(cli, stub, board):
    """--save-board would mutate the user's file. It must never be passed."""
    rec = stub()
    cli.drc(board)
    assert "--save-board" not in rec.argv


def test_drc_does_not_use_exit_code_violations(cli, stub, board):
    """With that flag, 'violations found' is indistinguishable from 'tool
    failed'. Violations come from the parsed report instead."""
    rec = stub()
    cli.drc(board)
    assert "--exit-code-violations" not in rec.argv


def test_drc_requests_json_and_all_severities(cli, stub, board):
    rec = stub()
    cli.drc(board)
    assert "--format" in rec.argv and "json" in rec.argv
    assert "--severity-all" in rec.argv


def test_drc_refills_zones_by_default(cli, stub, board):
    """Stale fills produce phantom unconnected items, corrupting the metric."""
    rec = stub()
    cli.drc(board)
    assert "--refill-zones" in rec.argv


def test_drc_refill_can_be_disabled(cli, stub, board):
    rec = stub()
    cli.drc(board, refill_zones=False)
    assert "--refill-zones" not in rec.argv


def test_drc_parity_opt_in(cli, stub, board):
    rec = stub()
    cli.drc(board)
    assert "--schematic-parity" not in rec.argv
    rec = stub()
    cli.drc(board, schematic_parity=True)
    assert "--schematic-parity" in rec.argv


def test_drc_passes_units(cli, stub, board):
    rec = stub()
    cli.drc(board, units="in")
    assert rec.argv[rec.argv.index("--units") + 1] == "in"


# --------------------------------------------------------------------------
# Failure handling


def test_missing_board_raises(cli, tmp_path):
    with pytest.raises(KicadCliError, match="board not found"):
        cli.drc(tmp_path / "nope.kicad_pcb")


def test_no_report_produced_raises_with_diagnostics(cli, stub, board):
    """The AppImage failure surfaced exactly this way: kicad-cli ran but wrote
    nothing. The error must include stdout/stderr or it's undebuggable."""
    stub(report=None, returncode=1)
    with pytest.raises(KicadCliError) as exc:
        cli.drc(board)
    msg = str(exc.value)
    assert "no report" in msg
    assert "stderr" in msg


def test_timeout_becomes_kicad_cli_error(cli, board, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="kicad-cli", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(KicadCliError, match="timed out"):
        cli.drc(board)


def test_missing_binary_raises_on_construction(monkeypatch):
    monkeypatch.setattr("autokicad.backend.shutil.which", lambda *a, **k: None)
    with pytest.raises(KicadCliError, match="not found on PATH"):
        CliBackend(exe="definitely-not-kicad-cli")


def test_render_failure_does_not_sink_observation(cli, stub, board, tmp_path):
    """A broken visual must never discard the structured findings -- they're the
    primary signal."""
    stub()  # DRC succeeds; render writes no PNG because the stub only honours -o for json
    obs = cli.observe(board, render=True, artifacts=tmp_path)
    assert obs.is_clean  # structured result survived
    assert any("failed" in p for p in obs.render_paths)


def test_backend_satisfies_protocol(cli):
    from autokicad.backend import Backend

    assert isinstance(cli, Backend)
