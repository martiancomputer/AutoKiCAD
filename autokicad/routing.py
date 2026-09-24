"""Headless routing harness and regression baselines.

Wraps the `pns_autoroute_hello` tool so routing behaviour can be exercised and
pinned from Python, reusing the same `Observation` model as the rest of the
package.

Why baselines rather than reviving upstream's corpus: `qa/tools/pns`'s
regression cases are not registered as a ctest (the `kicad_add_boost_test` line
is commented out), so upstream CI never ran them and the data desynced -- 2 of 8
cases pass. Those cases also pin *interactive* sessions replayed from recorded
logs, which is not what we are building. What we need guarded is: given a board
with copper stripped, does headless routing still connect nets without creating
DRC errors, and does it stay deterministic as the router changes.

    python -m autokicad.routing --record          # (re)record baselines
    python -m autokicad.routing --list            # show available boards
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .backend import CliBackend, KicadCliError, clean_env

REPO = Path(__file__).resolve().parents[1]
BOARD_DIR = REPO / "qa" / "data" / "pcbnew" / "pns_regressions" / "boards"
TOOL = REPO / "build" / "qa" / "tools" / "pns" / "pns_autoroute_hello"
KICAD_CLI = REPO / "build" / "kicad" / "kicad-cli"
BASELINE = Path(__file__).resolve().parent / "tests" / "fixtures" / "routing_baseline.json"

# Copper forms removed to produce an unrouted board.
_COPPER = re.compile(r"\((segment|arc|via)[\s(]")


class RoutingError(RuntimeError):
    """The routing tool could not be run, or produced unusable output."""


def build_env() -> dict[str, str]:
    """Environment for the built binaries.

    KICAD_RUN_FROM_BUILD_DIR makes KiCad resolve kifaces and stock data against
    the build tree rather than the install prefix; without it the tools fail to
    load `_pcbnew.kiface`. clean_env() additionally strips the AppImage vars.
    """
    env = clean_env()
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"
    return env


def strip_copper(src: Path | str, dst: Path | str) -> int:
    """Write `src` to `dst` with all segments, arcs and vias removed.

    Paren-matched rather than regex-replaced, so nested forms and quoted strings
    survive. Returns the number of forms removed.
    """
    text = Path(src).read_text(encoding="utf-8")
    out: list[str] = []
    i = removed = 0

    while i < len(text):
        if _COPPER.match(text, i):
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

    Path(dst).write_text("".join(out), encoding="utf-8")
    return removed


@dataclass
class RoutingResult:
    """What one headless routing run did."""

    board: str
    stripped: int = 0
    unrouted_before: int = 0
    unrouted_after: int = 0
    items_added: int = 0
    routed: int = 0
    exit_code: int = 0
    drc_errors_before: int | None = None
    drc_errors_after: int | None = None
    output: Path | None = field(default=None, repr=False)

    @property
    def drc_introduced(self) -> int | None:
        """Errors routing *added*. The stripped board is not necessarily clean,
        so the absolute count after routing says little on its own."""
        if self.drc_errors_before is None or self.drc_errors_after is None:
            return None
        return self.drc_errors_after - self.drc_errors_before

    @property
    def progress(self) -> int:
        """Connections closed. The metric an autorouter optimises."""
        return self.unrouted_before - self.unrouted_after

    def to_json(self) -> dict:
        return {
            "board": self.board,
            "stripped": self.stripped,
            "unrouted_before": self.unrouted_before,
            "unrouted_after": self.unrouted_after,
            "items_added": self.items_added,
            "routed": self.routed,
            "progress": self.progress,
            "drc_errors_before": self.drc_errors_before,
            "drc_errors_after": self.drc_errors_after,
            "drc_introduced": self.drc_introduced,
        }


_FIELDS = {
    "unrouted (before)": "unrouted_before",
    "unrouted (after)": "unrouted_after",
    "items added": "items_added",
}


def route(
    board: Path | str,
    workdir: Path | str,
    *,
    count: int = 5,
    check_drc: bool = True,
    timeout: int = 900,
) -> RoutingResult:
    """Strip a board, route `count` connections headlessly, optionally DRC it."""
    board = Path(board)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if not TOOL.is_file():
        raise RoutingError(
            f"{TOOL.relative_to(REPO)} not built. Configure with "
            "-DKICAD_BUILD_PNS_DEBUG_TOOL=ON and build pns_autoroute_hello."
        )

    unrouted = workdir / f"{board.stem}-unrouted.kicad_pcb"
    routed = workdir / f"{board.stem}-routed.kicad_pcb"

    result = RoutingResult(board=board.name, output=routed)
    result.stripped = strip_copper(board, unrouted)

    # Copy the project and rules beside the stripped board. Netclasses live in
    # the project, and without one the board loads but length/delay calculation
    # has no netclass to consult -- which is a crash, not a graceful degradation.
    import shutil

    for ext in (".kicad_pro", ".kicad_dru"):
        sidecar = board.with_suffix(ext)
        if sidecar.is_file():
            shutil.copy(sidecar, unrouted.with_suffix(ext))
            shutil.copy(sidecar, routed.with_suffix(ext))

    try:
        proc = subprocess.run(
            [str(TOOL), str(unrouted), "-o", str(routed), "-n", str(count)],
            env=build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RoutingError(f"routing {board.name} timed out after {timeout}s") from exc

    result.exit_code = proc.returncode

    for line in proc.stdout.splitlines():
        for label, attr in _FIELDS.items():
            if line.startswith(label):
                setattr(result, attr, int(line.split(":", 1)[1].strip()))
        if line.strip().startswith("net ") and "routed" in line:
            result.routed += 1

    if proc.returncode not in (0, 4):
        raise RoutingError(
            f"routing {board.name} failed (exit {proc.returncode}):\n"
            f"{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
        )

    if check_drc and routed.is_file():
        # Measure both boards. Stripping copper can itself leave a board with
        # violations, so only the difference attributes anything to routing.
        # Must use the matching binary: the tool writes a master-format board
        # that a released kicad-cli refuses to open.
        env_backup = os.environ.get("KICAD_RUN_FROM_BUILD_DIR")
        os.environ["KICAD_RUN_FROM_BUILD_DIR"] = "1"
        try:
            cli = CliBackend(exe=str(KICAD_CLI))
            # Refill zones: stale fills invent violations, which is precisely
            # the distortion this baseline must not bake in. Measured 8 errors
            # unfilled vs 3 filled on backspace1.
            result.drc_errors_before = cli.drc(unrouted).counts()["errors"]
            result.drc_errors_after = cli.drc(routed).counts()["errors"]
        except KicadCliError as exc:
            raise RoutingError(f"DRC on {board.name} failed: {exc}") from exc
        finally:
            if env_backup is None:
                os.environ.pop("KICAD_RUN_FROM_BUILD_DIR", None)
            else:
                os.environ["KICAD_RUN_FROM_BUILD_DIR"] = env_backup

    return result


_NET_TABLE = re.compile(r'\(net\s+(\d+)\s+"([^"]*)"\s*\)')
_NET_NAMED = re.compile(r'\(net\s+"([^"]*)"\s*\)')
_NET_CODE = re.compile(r"\(net\s+(\d+)\s*\)")


def _forms(text: str, keyword: str):
    """Yield each top-level `(keyword ...)` form, paren-matched.

    Indentation-agnostic on purpose: KiCad's older formatter writes a segment on
    one line with a numeric `(net 1)`, master writes it across several lines with
    `(net "name")`. A regex tuned to either layout silently reports the other as
    having no copper at all.
    """
    pattern = re.compile(r"\(" + keyword + r"[\s(]")
    i = 0

    while i < len(text):
        m = pattern.search(text, i)

        if not m:
            return

        depth, p, in_str = 0, m.start(), False

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

        yield text[m.start():p]
        i = p


def routed_nets(board: Path | str) -> list[str]:
    """Net names that actually have copper on them.

    Handles both board formats: a numeric net code is resolved through the
    board's net table.
    """
    text = Path(board).read_text(encoding="utf-8")
    codes = {int(c): n for c, n in _NET_TABLE.findall(text)}
    names: set[str] = set()

    for keyword in ("segment", "arc", "via"):
        for form in _forms(text, keyword):
            if m := _NET_NAMED.search(form):
                names.add(m.group(1))
            elif m := _NET_CODE.search(form):
                if name := codes.get(int(m.group(1))):
                    names.add(name)

    return sorted(n for n in names if n)


def write_skew_rule(board: Path | str, dru: Path | str, max_skew_nm: int) -> list[str]:
    """Write a `.kicad_dru` constraining skew across the board's routed nets.

    Scoped to routed nets deliberately. A rule matching a whole netclass sweeps
    in every *unrouted* net too; those have zero length, so each one reports a
    skew equal to the group maximum and the result says nothing about routing
    quality. On `simple.kicad_pcb` that inflated 5 real violations into 26.
    """
    nets = routed_nets(board)

    if not nets:
        raise RoutingError(f"{Path(board).name} has no routed nets to constrain")

    condition = " || ".join(f"A.NetName == '{n}'" for n in nets)
    Path(dru).write_text(
        "(version 1)\n"
        '(rule "bus_skew"\n'
        f"\t(constraint skew (max {max_skew_nm / 1e6:g}mm))\n"
        f'\t(condition "{condition}")\n'
        ")\n",
        encoding="utf-8",
    )
    return nets


def measure_skew(board: Path | str, *, max_skew_nm: int = 1_000_000) -> dict:
    """Route-then-grade: how far outside a skew budget is this board?

    Returns the violation count and the worst skew seen. This is the signal a
    length-tuning pass has to drive to zero; it does not itself tune anything.
    """
    board = Path(board)
    dru = board.with_suffix(".kicad_dru")
    nets = write_skew_rule(board, dru, max_skew_nm)

    env_backup = os.environ.get("KICAD_RUN_FROM_BUILD_DIR")
    os.environ["KICAD_RUN_FROM_BUILD_DIR"] = "1"
    try:
        obs = CliBackend(exe=str(KICAD_CLI)).drc(board)
    finally:
        if env_backup is None:
            os.environ.pop("KICAD_RUN_FROM_BUILD_DIR", None)
        else:
            os.environ["KICAD_RUN_FROM_BUILD_DIR"] = env_backup

    violations = [v for v in obs.of() if v.type == "skew_out_of_range"]
    worst = 0.0

    for v in violations:
        if m := re.search(r"actual (-?[\d.]+) mm", v.description):
            worst = max(worst, abs(float(m.group(1))))

    return {
        "nets": nets,
        "violations": len(violations),
        "worst_skew_mm": worst,
        "max_skew_mm": max_skew_nm / 1e6,
    }


def boards() -> list[Path]:
    return sorted(BOARD_DIR.glob("*.kicad_pcb")) if BOARD_DIR.is_dir() else []


def load_baseline() -> dict:
    if not BASELINE.is_file():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def record(selected: list[Path], workdir: Path, count: int) -> dict:
    entries = {}

    for board in selected:
        print(f"  {board.name} ... ", end="", flush=True)
        try:
            r = route(board, workdir, count=count)
        except RoutingError as exc:
            print(f"SKIP ({exc})")
            continue
        entries[board.name] = r.to_json()
        print(
            f"stripped={r.stripped} progress={r.progress} added={r.items_added} "
            f"drc={r.drc_errors_before}->{r.drc_errors_after} "
            f"(introduced {r.drc_introduced})"
        )

    return {"routed_count": count, "boards": entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true", help="write baselines")
    ap.add_argument("--list", action="store_true", help="list candidate boards")
    ap.add_argument("-n", "--count", type=int, default=5)
    ap.add_argument("--only", default="", help="comma-separated board name filter")
    ap.add_argument("-w", "--workdir", type=Path, default=Path("/tmp/autokicad-routing"))
    args = ap.parse_args(argv)

    found = boards()

    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        found = [b for b in found if b.name in wanted or b.stem in wanted]

    if args.list or not args.record:
        for b in found:
            print(f"{b.name:34s} {b.stat().st_size // 1024:6d} KiB")
        if not args.record:
            print(f"\n{len(found)} board(s). Use --record to write baselines.")
        return 0

    print(f"recording {len(found)} board(s), {args.count} connection(s) each")
    data = record(found, args.workdir, args.count)

    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {BASELINE.relative_to(REPO)} ({len(data['boards'])} board(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
