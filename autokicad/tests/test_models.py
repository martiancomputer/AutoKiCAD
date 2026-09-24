"""Domain-model tests: parsing, tallies, and the agent-facing rollup.

The highest-value test here is `test_errors_exclude_unconnected` -- that was a
real defect, not a hypothetical one. The first version of this harness reported
`errors=14 unconnected=14` for the same 14 findings, because KiCad tags
unconnected items with `severity: error`.
"""

from __future__ import annotations

import pytest

from autokicad.models import (
    AffectedItem,
    Category,
    Observation,
    Position,
    Severity,
    Violation,
)


# --------------------------------------------------------------------------
# Severity


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("error", Severity.ERROR),
        ("warning", Severity.WARNING),
        ("WARNING", Severity.WARNING),
        ("  Error  ", Severity.ERROR),
        ("exclusion", Severity.EXCLUSION),
        ("info", Severity.INFO),
        ("something_new", Severity.UNKNOWN),
        (None, Severity.UNKNOWN),
        ("", Severity.UNKNOWN),
    ],
)
def test_severity_parse(raw, expected):
    """Unknown severities must degrade, never raise -- KiCad may add new ones."""
    assert Severity.parse(raw) is expected


def test_severity_rank_orders_worst_first():
    ranks = [Severity.ERROR, Severity.WARNING, Severity.EXCLUSION, Severity.INFO]
    assert [s.rank for s in ranks] == sorted(s.rank for s in ranks)


# --------------------------------------------------------------------------
# Parsing quirks of KiCad's schema


def test_excluded_and_comment_absent_by_default():
    """rc_json_schema.h only emits `excluded`/`comment` when actually excluded,
    so `.at()`-style access would blow up. Must default cleanly."""
    v = Violation.from_json(
        {"type": "clearance", "description": "d", "severity": "error", "items": []},
        Category.VIOLATION,
    )
    assert v.excluded is False
    assert v.comment is None


def test_excluded_violation_round_trips(mixed):
    excluded = [v for v in mixed.violations if v.excluded]
    assert len(excluded) == 1
    assert excluded[0].comment == "reviewed, acceptable"


def test_missing_position_tolerated():
    v = Violation.from_json(
        {
            "type": "t",
            "description": "d",
            "severity": "error",
            "items": [{"uuid": "u", "description": "no pos"}],
        },
        Category.VIOLATION,
    )
    assert v.items[0].pos is None
    assert v.anchor is None


def test_malformed_position_tolerated():
    item = AffectedItem.from_json({"uuid": "u", "description": "d", "pos": {"x": 1}})
    assert item.pos is None


def test_anchor_picks_first_positioned_item():
    v = Violation.from_json(
        {
            "type": "t",
            "description": "d",
            "severity": "error",
            "items": [
                {"uuid": "a", "description": "unpositioned"},
                {"uuid": "b", "description": "positioned", "pos": {"x": 3, "y": 4}},
            ],
        },
        Category.VIOLATION,
    )
    assert v.anchor == Position(3.0, 4.0)


def test_unknown_type_defaults():
    v = Violation.from_json({}, Category.VIOLATION)
    assert v.type == "unknown"
    assert v.severity is Severity.UNKNOWN


# --------------------------------------------------------------------------
# Disjoint tallies -- the regression that matters


def test_errors_exclude_unconnected(mixed):
    """Regression: unconnected items carry severity=error but must not be
    double-counted in `errors`."""
    unconnected = mixed.of(category=Category.UNCONNECTED)
    assert unconnected, "fixture should contain unconnected items"
    assert all(v.severity is Severity.ERROR for v in unconnected)

    # None of them may leak into `errors`.
    assert all(v.category is not Category.UNCONNECTED for v in mixed.errors)


def test_counts_do_not_double_count(mixed):
    c = mixed.counts()
    # errors + warnings + unconnected must not exceed the real total.
    assert c["errors"] + c["warnings"] + c["unconnected"] <= c["total"]
    assert c["unconnected"] == len(mixed.of(category=Category.UNCONNECTED))


def test_counts_partition_total(mixed):
    """Every non-excluded finding lands in exactly one bucket."""
    c = mixed.counts()
    buckets = c["errors"] + c["warnings"] + c["unconnected"] + c["parity"]
    others = [
        v
        for v in mixed.of()
        if v.category is Category.VIOLATION
        and v.severity not in (Severity.ERROR, Severity.WARNING)
    ]
    assert buckets + len(others) == c["total"]


def test_excluded_absent_from_selectors(mixed):
    assert all(not v.excluded for v in mixed.of())
    assert any(v.excluded for v in mixed.violations)
    assert mixed.counts()["excluded"] == 1


def test_include_excluded_opt_in(mixed):
    assert len(mixed.of(include_excluded=True)) == len(mixed.of()) + 1


# --------------------------------------------------------------------------
# is_clean semantics


def test_clean_board_is_clean(clean):
    assert clean.is_clean
    assert clean.counts()["total"] == 0


def test_warnings_alone_are_clean(grouped):
    """Library mismatches shouldn't block an agent -- warnings are tolerated."""
    assert grouped.counts()["warnings"] > 0
    assert not grouped.errors
    assert grouped.is_clean


def test_unconnected_alone_is_not_clean():
    obs = Observation(
        source="s",
        violations=[
            Violation.from_json(
                {
                    "type": "unconnected_items",
                    "description": "Missing connection",
                    "severity": "error",
                    "items": [],
                },
                Category.UNCONNECTED,
            )
        ],
    )
    assert obs.unconnected_count == 1
    assert not obs.errors          # disjoint
    assert not obs.is_clean        # but still not shippable


def test_mixed_is_not_clean(mixed):
    assert not mixed.is_clean


# --------------------------------------------------------------------------
# Rollup / token collapse


def test_by_type_collapses_duplicates(grouped):
    rows = grouped.by_type(Category.VIOLATION)
    total = sum(n for _, _, n, _ in rows)
    assert total == len(grouped.of(category=Category.VIOLATION))
    # 40 identical violations must collapse to a single row.
    assert len(rows) < total


def test_by_type_sorted_worst_first_then_by_count():
    def mk(t, sev):
        return Violation.from_json(
            {"type": t, "description": "d", "severity": sev, "items": []},
            Category.VIOLATION,
        )

    obs = Observation(
        source="s",
        violations=[mk("w1", "warning")] * 5
        + [mk("e1", "error")]
        + [mk("e2", "error")] * 3,
    )
    rows = obs.by_type(Category.VIOLATION)
    assert [(t, n) for t, _, n, _ in rows] == [("e2", 3), ("e1", 1), ("w1", 5)]


def test_by_type_exemplar_belongs_to_group(grouped):
    for t, s, _n, exemplar in grouped.by_type(Category.VIOLATION):
        assert exemplar.type == t
        assert exemplar.severity is s


def test_category_filter_isolates(mixed):
    for cat in Category:
        for v in mixed.of(category=cat):
            assert v.category is cat


# --------------------------------------------------------------------------
# Agent text


def test_agent_text_is_compact(grouped):
    """40 findings must not produce 40 lines."""
    text = grouped.to_agent_text()
    assert len(text.splitlines()) < 20
    assert "Board:" in text and "Verdict:" in text


def test_agent_text_reports_verdict(clean, mixed):
    assert "CLEAN" in clean.to_agent_text()
    assert "NOT CLEAN" in mixed.to_agent_text()


def test_agent_text_surfaces_unrouted_first(mixed):
    text = mixed.to_agent_text()
    assert "Unrouted" in text
    # Routing gap should appear before generic violations.
    assert text.index("Unrouted") < text.index("Violations by type")


def test_agent_text_truncation_is_reported():
    def mk(i):
        return Violation.from_json(
            {"type": f"t{i}", "description": "d", "severity": "error", "items": []},
            Category.VIOLATION,
        )

    obs = Observation(source="s", violations=[mk(i) for i in range(30)])
    text = obs.to_agent_text(max_types=5)
    assert "more type(s)" in text


def test_agent_text_mentions_ignored_checks(mixed):
    if mixed.ignored_checks:
        assert "disabled" in mixed.to_agent_text()


def test_agent_text_lists_renders(clean):
    clean.render_paths.append("/tmp/x.png")
    assert "/tmp/x.png" in clean.to_agent_text()
