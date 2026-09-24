"""Command-line entry point: observe a board and print an agent-shaped summary.

    python -m autokicad.cli board.kicad_pcb
    python -m autokicad.cli board.kicad_pcb --layers F.Cu,B.Cu --render -a out/
    python -m autokicad.cli board.kicad_pcb --json

Exit codes: 0 clean, 1 errors or unrouted nets present, 2 tool failure.
Warnings alone do not fail the run.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .backend import CliBackend, KicadCliError
from .models import Observation


def _as_dict(obs: Observation) -> dict:
    d = dataclasses.asdict(obs)
    # Enums -> their string values, so the JSON is stable and diffable.
    for v in d["violations"]:
        v["severity"] = v["severity"].value if hasattr(v["severity"], "value") else str(v["severity"])
        v["category"] = v["category"].value if hasattr(v["category"], "value") else str(v["category"])
    d["counts"] = obs.counts()
    d["is_clean"] = obs.is_clean
    return d


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="autokicad", description="Observe a KiCad board: DRC + optional visuals."
    )
    p.add_argument("board", type=Path, help="path to a .kicad_pcb")
    p.add_argument("--layers", default="", help="comma-separated layers to plot as SVG, e.g. F.Cu,B.Cu")
    p.add_argument("--render", action="store_true", help="also produce a 3D PNG")
    p.add_argument("-a", "--artifacts", type=Path, default=None, help="directory for visuals")
    p.add_argument("--parity", action="store_true", help="also check schematic parity")
    p.add_argument("--no-refill", action="store_true", help="skip zone refill (faster, less accurate connectivity)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--max-types", type=int, default=12, help="distinct violation types to show")
    p.add_argument("--exe", default="kicad-cli", help="kicad-cli binary to use")
    args = p.parse_args(argv)

    try:
        backend = CliBackend(exe=args.exe)
        obs = backend.observe(
            args.board,
            render=args.render,
            layers=[s for s in args.layers.split(",") if s.strip()] or None,
            artifacts=args.artifacts,
            schematic_parity=args.parity,
            refill_zones=not args.no_refill,
        )
    except KicadCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_as_dict(obs), indent=2))
    else:
        print(obs.to_agent_text(max_types=args.max_types))

    return 0 if obs.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
