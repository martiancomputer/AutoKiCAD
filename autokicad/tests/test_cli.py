"""CLI contract tests: exit codes and output shape.

Exit codes are the interface an agent (or CI) actually consumes, so they are
pinned: 0 clean, 1 findings, 2 tool failure.
"""

from __future__ import annotations

import json

import pytest

from autokicad.backend import KicadCliError
from autokicad.cli import _as_dict, main
from .conftest import load


@pytest.fixture
def fake_observe(monkeypatch):
    """Replace the backend entirely so CLI tests never touch KiCad."""

    def install(fixture: str | None, *, raises: bool = False):
        from autokicad.backend import CliBackend

        monkeypatch.setattr(CliBackend, "__post_init__", lambda self: None)

        if raises:
            def observe(self, board, **kw):
                raise KicadCliError("simulated failure")
        else:
            def observe(self, board, **kw):
                return CliBackend._parse_drc(load(fixture), fallback_source=str(board))

        monkeypatch.setattr(CliBackend, "observe", observe)

    return install


def test_exit_zero_when_clean(fake_observe, capsys):
    fake_observe("drc_clean")
    assert main(["board.kicad_pcb"]) == 0
    assert "CLEAN" in capsys.readouterr().out


def test_exit_one_when_findings(fake_observe, capsys):
    fake_observe("drc_mixed")
    assert main(["board.kicad_pcb"]) == 1
    assert "NOT CLEAN" in capsys.readouterr().out


def test_exit_zero_for_warnings_only(fake_observe):
    """Warnings must not fail the run, or every board fails."""
    fake_observe("drc_grouped")
    assert main(["board.kicad_pcb"]) == 0


def test_exit_two_on_tool_failure(fake_observe, capsys):
    fake_observe(None, raises=True)
    assert main(["board.kicad_pcb"]) == 2
    assert "error:" in capsys.readouterr().err


def test_json_mode_is_valid_json(fake_observe, capsys):
    fake_observe("drc_mixed")
    main(["board.kicad_pcb", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["unconnected"] > 0
    assert payload["is_clean"] is False


def test_json_enums_are_strings(fake_observe, capsys):
    """Raw Enum members aren't JSON-serialisable; they must be flattened."""
    fake_observe("drc_mixed")
    main(["board.kicad_pcb", "--json"])
    payload = json.loads(capsys.readouterr().out)
    for v in payload["violations"]:
        assert isinstance(v["severity"], str)
        assert isinstance(v["category"], str)


def test_as_dict_round_trips(mixed):
    """_as_dict output must survive json.dumps -- guards the enum flattening."""
    restored = json.loads(json.dumps(_as_dict(mixed)))
    assert restored["counts"] == mixed.counts()
    assert len(restored["violations"]) == len(mixed.violations)


def test_max_types_limits_output(fake_observe, capsys):
    fake_observe("drc_grouped")
    main(["board.kicad_pcb", "--max-types", "1"])
    long = len(capsys.readouterr().out.splitlines())

    fake_observe("drc_grouped")
    main(["board.kicad_pcb", "--max-types", "50"])
    assert len(capsys.readouterr().out.splitlines()) >= long
