"""Shared fixtures.

Unit tests read captured DRC JSON and never invoke KiCad, so they are fast and
deterministic. Tests that do shell out are marked `integration` and skip
automatically when kicad-cli is unavailable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from autokicad.backend import CliBackend, clean_env

FIXTURES = Path(__file__).parent / "fixtures"
DEMOS = Path(__file__).resolve().parents[2] / "demos"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requires a working kicad-cli binary"
    )
    config.addinivalue_line(
        "markers", "ipc_live: spawns a real kicad-cli api-server (master build only)"
    )
    config.addinivalue_line(
        "markers",
        "schematic_live: spawns eeschema on a private Xvfb (needs an X server)",
    )
    config.addinivalue_line(
        "markers", "routing: runs headless routing against recorded baselines (slow)"
    )


def _have_kicad_cli() -> bool:
    # Must use the cleaned PATH for the same reason backend does.
    return shutil.which("kicad-cli", path=clean_env().get("PATH")) is not None


requires_cli = pytest.mark.skipif(
    not _have_kicad_cli(), reason="kicad-cli not installed"
)


@pytest.fixture(scope="session")
def backend() -> CliBackend:
    return CliBackend()


@pytest.fixture
def mixed():
    """Real unrouted-board report: unconnected + violations + parity + excluded."""
    return CliBackend._parse_drc(load("drc_mixed"), fallback_source="mixed.kicad_pcb")


@pytest.fixture
def grouped():
    """40 violations that are all the same type -- the collapse case."""
    return CliBackend._parse_drc(load("drc_grouped"), fallback_source="g.kicad_pcb")


@pytest.fixture
def clean():
    return CliBackend._parse_drc(load("drc_clean"), fallback_source="clean.kicad_pcb")
