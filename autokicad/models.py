"""Transport-independent domain model for board observations.

These types deliberately know nothing about *how* the data was obtained. The
CLI backend populates them today; the IPC/protobuf backend will populate the
same types once `kicad-cli api-server` is available in the dev build. Agent-facing
code should only ever see these types, never raw JSON or protobuf messages.

Field names and optionality mirror KiCad's own schema in
`include/rc_json_schema.h` (RC_JSON namespace). Note that `excluded` and
`comment` are only emitted when a violation is actually excluded, so both are
optional here -- verified against real `kicad-cli pcb drc` output.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """DRC severities as emitted by KiCad. Ordered worst-first for sorting."""

    ERROR = "error"
    WARNING = "warning"
    EXCLUSION = "exclusion"
    INFO = "info"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> Severity:
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return cls.UNKNOWN

    @property
    def rank(self) -> int:
        order = {
            Severity.ERROR: 0,
            Severity.WARNING: 1,
            Severity.EXCLUSION: 2,
            Severity.INFO: 3,
            Severity.UNKNOWN: 4,
        }
        return order[self]


class Category(str, Enum):
    """Which DRC bucket a violation came from.

    KiCad reports these as three separate arrays rather than as a field on the
    violation, so we attach it during parsing. UNCONNECTED is the routing
    progress signal -- it is what "how far has routing gotten" actually means.
    """

    VIOLATION = "violation"
    UNCONNECTED = "unconnected"
    PARITY = "schematic_parity"


@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float

    def __str__(self) -> str:
        return f"({self.x:.3f}, {self.y:.3f})"


@dataclass(frozen=True, slots=True)
class AffectedItem:
    """A board item implicated in a violation. `uuid` is the KIID."""

    uuid: str
    description: str
    pos: Position | None

    @classmethod
    def from_json(cls, raw: dict) -> AffectedItem:
        pos_raw = raw.get("pos")
        pos = None
        if isinstance(pos_raw, dict) and "x" in pos_raw and "y" in pos_raw:
            pos = Position(float(pos_raw["x"]), float(pos_raw["y"]))
        return cls(
            uuid=str(raw.get("uuid", "")),
            description=str(raw.get("description", "")),
            pos=pos,
        )


@dataclass(frozen=True, slots=True)
class Violation:
    type: str
    description: str
    severity: Severity
    category: Category
    items: tuple[AffectedItem, ...]
    excluded: bool
    comment: str | None

    @classmethod
    def from_json(cls, raw: dict, category: Category) -> Violation:
        return cls(
            type=str(raw.get("type", "unknown")),
            description=str(raw.get("description", "")),
            severity=Severity.parse(raw.get("severity")),
            category=category,
            # `excluded`/`comment` are absent unless the violation is excluded.
            items=tuple(AffectedItem.from_json(i) for i in raw.get("items", [])),
            excluded=bool(raw.get("excluded", False)),
            comment=raw.get("comment"),
        )

    @property
    def anchor(self) -> Position | None:
        """First positioned item -- where an agent should look on the board."""
        for item in self.items:
            if item.pos is not None:
                return item.pos
        return None


@dataclass(frozen=True, slots=True)
class IgnoredCheck:
    key: str
    description: str


@dataclass(slots=True)
class Observation:
    """One complete look at a board: what's wrong, and optionally what it looks like.

    This is the agent's observe step. `render_paths` is populated only when a
    visual was requested; the structured fields are always the primary signal.
    """

    source: str
    kicad_version: str = ""
    coordinate_units: str = "mm"
    violations: list[Violation] = field(default_factory=list)
    ignored_checks: list[IgnoredCheck] = field(default_factory=list)
    included_severities: list[str] = field(default_factory=list)
    render_paths: list[str] = field(default_factory=list)

    # ---- selectors -------------------------------------------------------

    def of(
        self,
        *,
        category: Category | None = None,
        severity: Severity | None = None,
        include_excluded: bool = False,
    ) -> list[Violation]:
        out = self.violations
        if not include_excluded:
            out = [v for v in out if not v.excluded]
        if category is not None:
            out = [v for v in out if v.category is category]
        if severity is not None:
            out = [v for v in out if v.severity is severity]
        return out

    @property
    def errors(self) -> list[Violation]:
        """Error-severity *board rule* violations only.

        Scoped to Category.VIOLATION deliberately. KiCad gives both
        `unconnected_items` and `schematic_parity` findings `severity: error`,
        so a severity-only filter double-reports them alongside
        `unconnected_count` and the parity tally. Restricting by category makes
        errors / warnings / unconnected / parity a true partition, so an agent
        can sum them without inflating the total.

        The three concerns are also genuinely different: a rule violation means
        the copper is wrong, unconnected means routing is unfinished, and parity
        means the board disagrees with the schematic.
        """
        return self.of(category=Category.VIOLATION, severity=Severity.ERROR)

    @property
    def warnings(self) -> list[Violation]:
        return self.of(category=Category.VIOLATION, severity=Severity.WARNING)

    @property
    def unconnected_count(self) -> int:
        """Primary routing-progress metric. Zero means fully routed."""
        return len(self.of(category=Category.UNCONNECTED))

    @property
    def is_clean(self) -> bool:
        """No errors and nothing unrouted. Warnings are tolerated."""
        return not self.errors and self.unconnected_count == 0

    # ---- agent-facing rollups -------------------------------------------

    def by_type(
        self, category: Category | None = None
    ) -> list[tuple[str, Severity, int, Violation]]:
        """Group by (type, severity) with counts and one exemplar each.

        This is the whole point of the harness: a board with 129 identical
        warnings should cost an agent one line, not 129. Sorted worst-first,
        then by descending count.
        """
        groups: dict[tuple[str, Severity], list[Violation]] = {}
        for v in self.of(category=category):
            groups.setdefault((v.type, v.severity), []).append(v)

        rows = [(t, s, len(vs), vs[0]) for (t, s), vs in groups.items()]
        rows.sort(key=lambda r: (r[1].rank, -r[2], r[0]))
        return rows

    def counts(self) -> dict[str, int]:
        """Disjoint tallies -- `errors`/`warnings` exclude unrouted nets, so
        errors + warnings + unconnected never double-counts a finding."""
        return {
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "unconnected": self.unconnected_count,
            "parity": len(self.of(category=Category.PARITY)),
            "total": len(self.of()),
            "excluded": sum(1 for v in self.violations if v.excluded),
        }

    def to_agent_text(self, max_types: int = 12, max_examples: int = 2) -> str:
        """Compact, token-efficient summary intended to go straight to a model."""
        c = self.counts()
        verdict = "CLEAN" if self.is_clean else "NOT CLEAN"
        lines = [
            f"Board: {self.source}",
            f"Verdict: {verdict} | errors={c['errors']} warnings={c['warnings']} "
            f"unconnected={c['unconnected']} parity={c['parity']}"
            + (f" excluded={c['excluded']}" if c["excluded"] else ""),
        ]

        if c["unconnected"]:
            lines.append(f"\nUnrouted ({c['unconnected']}):")
            for t, s, n, ex in self.by_type(Category.UNCONNECTED)[:max_types]:
                lines.append(f"  {n:4d}x {t}: {ex.description}")

        rows = self.by_type(Category.VIOLATION)
        if rows:
            lines.append(f"\nViolations by type ({len(rows)} distinct):")
            for t, s, n, ex in rows[:max_types]:
                where = f" @ {ex.anchor}" if ex.anchor else ""
                lines.append(f"  {n:4d}x [{s.value}] {t}{where}")
                for item in ex.items[:max_examples]:
                    lines.append(f"          e.g. {item.description}")
            if len(rows) > max_types:
                lines.append(f"  ... {len(rows) - max_types} more type(s)")

        parity = self.by_type(Category.PARITY)
        if parity:
            lines.append(f"\nSchematic parity ({c['parity']}):")
            for t, s, n, ex in parity[:max_types]:
                lines.append(f"  {n:4d}x [{s.value}] {t}: {ex.description}")

        if self.render_paths:
            lines.append("\nRenders: " + ", ".join(self.render_paths))

        if self.ignored_checks:
            lines.append(
                f"\nNote: {len(self.ignored_checks)} check(s) disabled in this "
                "board's severities config (e.g. "
                + ", ".join(ic.key for ic in self.ignored_checks[:3])
                + ")"
            )
        return "\n".join(lines)
