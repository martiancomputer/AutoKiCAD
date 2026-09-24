"""End-to-end tests against a real kicad-cli and real demo boards.

Skipped automatically when kicad-cli is absent. These are slower (DRC with zone
refill on a real board), so they are also marked `integration` and can be
deselected with `-m "not integration"`.
"""

from __future__ import annotations

import pytest

from autokicad.models import Category, Severity
from .conftest import DEMOS, requires_cli

pytestmark = [pytest.mark.integration, requires_cli]


def _demo(name: str):
    path = DEMOS / name
    if not path.is_file():
        pytest.skip(f"demo board not present: {name}")
    return path


def test_version_reports_something(backend):
    assert backend.version()


def test_clean_demo_board(backend):
    """StickHub is clean upstream -- if this fails, either the board changed or
    our severity handling regressed."""
    obs = backend.observe(_demo("stickhub/StickHub.kicad_pcb"))
    assert obs.is_clean
    assert obs.unconnected_count == 0
    assert obs.kicad_version


def test_board_with_real_errors(backend):
    """microwave has genuine copper errors (shorting_items / clearance)."""
    obs = backend.observe(_demo("microwave/microwave.kicad_pcb"))
    assert not obs.is_clean
    assert obs.errors
    types = {v.type for v in obs.errors}
    assert types & {"shorting_items", "clearance"}
    # Fully routed, so the routing metric must stay clean.
    assert obs.unconnected_count == 0


def test_many_duplicate_warnings_collapse(backend):
    """The video demo emits ~129 identical library-mismatch warnings."""
    obs = backend.observe(_demo("video/video.kicad_pcb"))
    rows = obs.by_type(Category.VIOLATION)
    assert len(obs.of(category=Category.VIOLATION)) > 50
    assert len(rows) <= 3
    assert len(obs.to_agent_text().splitlines()) < 20


def test_unconnected_detected_on_stripped_board(backend, tmp_path):
    """Strip all copper from a routed board; every net should report unrouted.

    This exercises the routing-progress metric, which no shipped demo board
    does (they are all fully routed).
    """
    import re

    src = _demo("ecc83/ecc83-pp.kicad_pcb")
    text = src.read_text(encoding="utf-8")

    # Paren-matched removal of top-level (segment|arc|via ...) forms.
    out, i, removed = [], 0, 0
    while i < len(text):
        m = re.match(r"\((segment|arc|via)[\s(]", text[i:])
        if m:
            depth, p, in_str = 0, i, False
            while p < len(text):
                c = text[p]
                if in_str:
                    if c == "\\":
                        p += 2
                        continue
                    if c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        p += 1
                        break
                p += 1
            removed += 1
            i = p
            continue
        out.append(text[i])
        i += 1

    assert removed > 0, "fixture board had no copper to strip"

    stripped = tmp_path / "unrouted.kicad_pcb"
    stripped.write_text("".join(out), encoding="utf-8")

    obs = backend.observe(stripped)
    assert obs.unconnected_count > 0
    assert not obs.is_clean
    # Disjointness must hold on real data too, not just fixtures.
    assert all(v.category is not Category.UNCONNECTED for v in obs.errors)
    assert all(
        v.severity is Severity.ERROR for v in obs.of(category=Category.UNCONNECTED)
    )


def test_svg_layer_plots_produced(backend, tmp_path):
    paths = backend.plot_layers(
        _demo("ecc83/ecc83-pp.kicad_pcb"), tmp_path, ["F.Cu", "B.Cu"]
    )
    assert len(paths) >= 2
    assert all(p.suffix == ".svg" and p.stat().st_size > 0 for p in paths)


def test_render_produces_image(backend, tmp_path):
    out = backend.render(
        _demo("ecc83/ecc83-pp.kicad_pcb"), tmp_path / "top.png", width=400, height=300
    )
    assert out.is_file()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_observe_attaches_visuals(backend, tmp_path):
    obs = backend.observe(
        _demo("ecc83/ecc83-pp.kicad_pcb"),
        layers=["F.Cu"],
        artifacts=tmp_path,
    )
    assert obs.render_paths
    assert not any("failed" in p for p in obs.render_paths)


def test_board_file_is_not_modified(backend, tmp_path):
    """DRC with zone refill must not rewrite the board on disk."""
    import hashlib
    import shutil

    src = _demo("ecc83/ecc83-pp.kicad_pcb")
    copy = tmp_path / "b.kicad_pcb"
    shutil.copy(src, copy)
    before = hashlib.sha256(copy.read_bytes()).hexdigest()

    backend.drc(copy, refill_zones=True)

    assert hashlib.sha256(copy.read_bytes()).hexdigest() == before
