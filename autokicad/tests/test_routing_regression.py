"""Routing regression net.

Guards headless routing against the recorded baselines in
`fixtures/routing_baseline.json`. Assertions are one-sided on purpose:

  * progress (connections closed) may **improve**, never regress
  * DRC errors introduced may **decrease**, never increase

so work on the router is free to make things better and is stopped from making
them quietly worse. When a run beats the baseline the test says so and asks for a
re-record, rather than silently accepting the new behaviour.

Marked `routing` as well as `integration`:

    pytest autokicad/tests -m routing            # just these
    pytest autokicad/tests -m "not routing"      # skip them (they are slow)

Re-record after an intentional change:

    python -m autokicad.routing --record -n 5
"""

from __future__ import annotations

import pytest

from autokicad.routing import (
    BOARD_DIR,
    KICAD_CLI,
    TOOL,
    RoutingError,
    boards,
    load_baseline,
    route,
    strip_copper,
)

pytestmark = [pytest.mark.integration, pytest.mark.routing]

BASELINE = load_baseline()


def _why_skip() -> str | None:
    if not TOOL.is_file():
        return "pns_autoroute_hello not built"
    if not KICAD_CLI.is_file():
        return "built kicad-cli not present"
    if not BOARD_DIR.is_dir():
        return "PNS regression boards missing"
    if not BASELINE:
        return "no baseline recorded (python -m autokicad.routing --record)"
    return None


skip_reason = _why_skip()
pytestmark.append(pytest.mark.skipif(skip_reason is not None, reason=skip_reason or ""))

CASES = sorted(BASELINE.get("boards", {})) if BASELINE else []


# --------------------------------------------------------------------------
# Stripping -- the fixture generator the whole suite depends on


def test_strip_copper_removes_all_copper(tmp_path):
    src = BOARD_DIR / "backspace1.kicad_pcb"
    dst = tmp_path / "stripped.kicad_pcb"

    removed = strip_copper(src, dst)
    text = dst.read_text(encoding="utf-8")

    assert removed > 0
    assert "(segment" not in text
    # Everything else must survive: a stripper that ate footprints would make
    # every downstream number meaningless.
    assert "(footprint" in text
    assert "(net " in text


def test_strip_copper_is_deterministic(tmp_path):
    src = BOARD_DIR / "backspace1.kicad_pcb"
    a, b = tmp_path / "a.kicad_pcb", tmp_path / "b.kicad_pcb"
    assert strip_copper(src, a) == strip_copper(src, b)
    assert a.read_bytes() == b.read_bytes()


# --------------------------------------------------------------------------
# Per-board regressions


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """Route every baselined board once and share the results."""
    workdir = tmp_path_factory.mktemp("routing")
    count = BASELINE.get("routed_count", 5)
    out = {}

    for board in boards():
        if board.name not in BASELINE.get("boards", {}):
            continue
        try:
            out[board.name] = route(board, workdir, count=count)
        except RoutingError as exc:
            out[board.name] = exc

    return out


@pytest.mark.parametrize("name", CASES)
def test_routing_completes(results, name):
    r = results[name]

    if isinstance(r, RoutingError):
        pytest.fail(f"{name}: {r}")

    # 0 = routed something, 4 = ran cleanly but routed nothing. Anything else
    # (notably 139) means a crash.
    assert r.exit_code in (0, 4), f"{name} exited {r.exit_code}"


@pytest.mark.parametrize("name", CASES)
def test_progress_does_not_regress(results, name):
    r = results[name]

    if isinstance(r, RoutingError):
        pytest.skip(str(r))

    expected = BASELINE["boards"][name]["progress"]

    assert r.progress >= expected, (
        f"{name}: closed {r.progress} connections, baseline {expected}"
    )

    if r.progress > expected:
        pytest.fail(
            f"{name}: routing IMPROVED ({expected} -> {r.progress}). "
            "Re-record: python -m autokicad.routing --record"
        )


@pytest.mark.parametrize("name", CASES)
def test_drc_errors_introduced_do_not_regress(results, name):
    """The number the real autorouter has to drive to zero.

    Absolute counts are not used: several boards have pre-existing violations
    (stickhub-extra-via starts at 54), so only what routing *adds* is ours.
    """
    r = results[name]

    if isinstance(r, RoutingError):
        pytest.skip(str(r))

    expected = BASELINE["boards"][name]["drc_introduced"]

    if expected is None or r.drc_introduced is None:
        pytest.skip("no DRC measurement recorded")

    assert r.drc_introduced <= expected, (
        f"{name}: routing introduced {r.drc_introduced} DRC errors, "
        f"baseline {expected}"
    )

    if r.drc_introduced < expected:
        pytest.fail(
            f"{name}: routing got CLEANER ({expected} -> {r.drc_introduced} "
            "errors introduced). Re-record: python -m autokicad.routing --record"
        )


@pytest.mark.parametrize("name", CASES)
def test_items_added_matches_progress(results, name):
    """Copper must actually reach the board.

    The original write-back bug reported successful routes with zero items
    added, because CommitRouting() was called after StopRouting(). Progress
    without items would mean that regressed.
    """
    r = results[name]

    if isinstance(r, RoutingError):
        pytest.skip(str(r))

    if r.progress > 0:
        assert r.items_added > 0, f"{name}: closed connections but added no copper"


# --------------------------------------------------------------------------
# Determinism


def test_routing_is_deterministic(tmp_path):
    """Two runs of the same board must agree.

    A search-based autorouter that is not reproducible cannot be regression
    tested at all, so this is worth pinning before one exists.
    """
    board = BOARD_DIR / "backspace1.kicad_pcb"

    a = route(board, tmp_path / "a", count=3, check_drc=False)
    b = route(board, tmp_path / "b", count=3, check_drc=False)

    assert (a.progress, a.items_added, a.unrouted_after) == (
        b.progress,
        b.items_added,
        b.unrouted_after,
    )
    assert (tmp_path / "a" / f"{board.stem}-routed.kicad_pcb").is_file()


def test_baseline_covers_every_board():
    """A board added to the corpus without a baseline would silently go
    unguarded."""
    recorded = set(BASELINE.get("boards", {}))
    available = {b.name for b in boards()}
    assert available - recorded == set(), (
        f"boards without baselines: {sorted(available - recorded)}"
    )


# --------------------------------------------------------------------------
# Skew measurement -- the grading half of step 8


def test_routed_nets_parses_master_format(tmp_path):
    """Master stores a net *name* on each segment, not the numeric code older
    formats used. A regex written for `(net 3)` matches nothing and silently
    reports a board as having no routed nets."""
    from autokicad.routing import routed_nets

    board = BOARD_DIR / "simple.kicad_pcb"
    nets = routed_nets(board)

    assert nets, "pre-routed demo board should report routed nets"
    assert all(isinstance(n, str) and n for n in nets)


def test_skew_rule_is_scoped_to_routed_nets(tmp_path):
    """A netclass-wide skew rule sweeps in unrouted nets, whose zero length makes
    each report a skew equal to the group maximum. On simple.kicad_pcb that
    turned 5 real violations into 26."""
    from autokicad.routing import routed_nets, write_skew_rule

    board = BOARD_DIR / "simple.kicad_pcb"
    dru = tmp_path / "r.kicad_dru"
    nets = write_skew_rule(board, dru, 1_000_000)

    text = dru.read_text(encoding="utf-8")
    assert "constraint skew" in text
    assert len(nets) == text.count("A.NetName ==")
    assert set(nets) == set(routed_nets(board))


def test_skew_is_measurable_after_routing(tmp_path):
    """Route a bus, then grade it against a skew budget.

    This is the signal a tuning pass must drive to zero. It does not tune
    anything -- the router has no length awareness yet.
    """
    from autokicad.routing import measure_skew

    board = BOARD_DIR / "simple.kicad_pcb"
    r = route(board, tmp_path, count=8, check_drc=False)

    if r.progress == 0:
        pytest.skip("nothing routed; skew is not meaningful")

    result = measure_skew(r.output, max_skew_nm=1_000_000)

    assert result["nets"], "routed board should expose nets to constrain"
    # Untuned routing has no reason to match lengths, so a tight budget must be
    # violated. If this ever passes, either tuning landed or the budget is not
    # actually being evaluated -- both worth knowing.
    assert result["violations"] > 0
    assert result["worst_skew_mm"] > result["max_skew_mm"]
