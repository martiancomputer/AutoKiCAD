#!/usr/bin/env python3
"""Generate documentation/01-api-inventory.md from the KiCad source tree.

The inventory is generated, never hand-edited, so it cannot drift from the code.
It cross-references two independent facts:

  1. What the schema *declares*  -- `message` blocks in api/proto/**
  2. What the code actually *handles* -- `registerHandler<Request, Response>`
     calls in the C++ API handlers

The gap between those two is the interesting part: a message existing in the
schema does not mean KiCad answers it.

    python documentation/tools/gen_api_inventory.py            # write the doc
    python documentation/tools/gen_api_inventory.py --check     # non-zero if stale
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROTO_ROOT = REPO / "api" / "proto"
DOC = REPO / "documentation" / "01-api-inventory.md"

# Handler file -> (surface label, how a client reaches it)
SURFACES = {
    "common/api/api_handler_common.cpp": (
        "COMMON",
        "Always available, no document required",
    ),
    "common/api/api_handler_editor.cpp": (
        "EDITOR (base)",
        "Inherited by BOTH board and schematic handlers",
    ),
    "pcbnew/api/api_handler_board.cpp": ("BOARD", "An open .kicad_pcb"),
    "pcbnew/api/api_handler_pcb.cpp": ("PCB APP", "The pcbnew application itself"),
    "pcbnew/api/api_handler_footprint.cpp": (
        "FOOTPRINT",
        "The footprint editor",
    ),
    "eeschema/api/api_handler_sch.cpp": ("SCHEMATIC", "An open .kicad_sch"),
}

MSG_RE = re.compile(r"^\s*message\s+(\w+)", re.MULTILINE)
PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
# registerHandler<Request, Response>( ... )  -- Response may be namespace-qualified
REG_RE = re.compile(
    r"registerHandler\s*<\s*([\w:]+)\s*(?:,\s*([\w:]+)\s*)?>", re.DOTALL
)


@dataclass
class ProtoFile:
    path: Path
    package: str
    messages: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(PROTO_ROOT))


def scan_protos() -> list[ProtoFile]:
    out = []
    for p in sorted(PROTO_ROOT.rglob("*.proto")):
        text = p.read_text(encoding="utf-8")
        pkg = m.group(1) if (m := PKG_RE.search(text)) else "?"
        out.append(ProtoFile(p, pkg, MSG_RE.findall(text)))
    return out


def scan_handlers() -> dict[str, list[tuple[str, str]]]:
    """handler file -> [(request, response)]"""
    handlers: dict[str, list[tuple[str, str]]] = {}
    for rel in SURFACES:
        f = REPO / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        seen: list[tuple[str, str]] = []
        for req, resp in REG_RE.findall(text):
            req = req.split("::")[-1]
            resp = (resp or "").split("::")[-1] or "-"
            if (req, resp) not in seen:
                seen.append((req, resp))
        handlers[rel] = sorted(seen)
    return handlers


def cli_subcommands() -> list[tuple[str, str]]:
    """Ask the built kicad-cli what it supports; fall back to source scan."""
    exe = REPO / "build" / "kicad" / "kicad-cli"
    if not exe.is_file():
        return []
    import os

    env = {k: v for k, v in os.environ.items()
           if k not in ("APPDIR", "APPIMAGE", "OWD", "ARGV0")}
    try:
        proc = subprocess.run([str(exe), "--help"], capture_output=True, text=True,
                              timeout=60, env=env)
    except Exception:
        return []

    out, seen_header = [], False
    for line in proc.stdout.splitlines():
        if line.strip().startswith("Subcommands"):
            seen_header = True
            continue
        if seen_header and (m := re.match(r"\s+(\S+)\s{2,}(.*)", line)):
            out.append((m.group(1), m.group(2).strip()))
    return out


def kicad_version() -> str:
    f = REPO / "cmake" / "KiCadVersion.cmake"
    if f.is_file() and (m := re.search(r'KICAD_SEMANTIC_VERSION\s+"([^"]+)"',
                                       f.read_text(encoding="utf-8"))):
        return m.group(1)
    return "unknown"


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "unknown"


def render() -> str:
    protos = scan_protos()
    handlers = scan_handlers()
    cli = cli_subcommands()

    handled: dict[str, list[str]] = defaultdict(list)
    for rel, pairs in handlers.items():
        label = SURFACES[rel][0]
        for req, _resp in pairs:
            handled[req].append(label)

    all_msgs = {m for p in protos for m in p.messages}
    total_handled = len(set(handled) & all_msgs)

    L: list[str] = []
    w = L.append

    w("# API inventory")
    w("")
    w("> **Generated file — do not edit.**")
    w("> `python documentation/tools/gen_api_inventory.py`")
    w(f"> KiCad `{kicad_version()}` @ `{git_head()}` · regenerated {date.today().isoformat()}")
    w("")
    w("Two independent facts, cross-referenced: what the protobuf schema "
      "*declares*, and what the C++ actually *handles*. A message existing in "
      "the schema does **not** mean KiCad answers it.")
    w("")
    w("## Summary")
    w("")
    w("| | Count |")
    w("|---|---|")
    w(f"| `.proto` files | {len(protos)} |")
    w(f"| Messages declared | {len(all_msgs)} |")
    w(f"| Messages with a handler | {total_handled} |")
    w(f"| Handler surfaces | {len(handlers)} |")
    w(f"| `kicad-cli` subcommands | {len(cli) or 'n/a (build not found)'} |")
    w("")
    w("Most declared messages are *types* and *responses*, which never need a "
      "handler. Handler count is the honest measure of the callable surface.")
    w("")

    # ---- handler surfaces
    w("## Handler surfaces")
    w("")
    w("Which handler answers a request depends on the document it targets. "
      "`EDITOR (base)` is inherited by both board and schematic handlers, which "
      "is why generic item CRUD works on either.")
    w("")
    for rel, (label, reach) in SURFACES.items():
        pairs = handlers.get(rel)
        if pairs is None:
            continue
        w(f"### {label} — `{rel}`")
        w("")
        w(f"*{reach}* · **{len(pairs)} commands**")
        w("")
        w("| Request | Response |")
        w("|---|---|")
        for req, resp in pairs:
            w(f"| `{req}` | `{resp}` |")
        w("")

    # ---- schema by file
    w("## Schema by file")
    w("")
    w("`H` marks a message with a registered handler.")
    w("")
    for p in protos:
        hits = [m for m in p.messages if m in handled]
        w(f"### `{p.rel}`")
        w("")
        w(f"package `{p.package}` · {len(p.messages)} message(s) · "
          f"{len(hits)} handled")
        w("")
        if p.messages:
            w("| Message | Handled by |")
            w("|---|---|")
            for m in p.messages:
                where = ", ".join(handled.get(m, [])) or "—"
                mark = "**H**" if m in handled else ""
                w(f"| `{m}` {mark} | {where} |")
            w("")

    # ---- cli
    if cli:
        w("## `kicad-cli` subcommands")
        w("")
        w("Independent of the IPC API: a separate process, reading files from "
          "disk. Useful when no editor is running.")
        w("")
        w("| Subcommand | Description |")
        w("|---|---|")
        for name, desc in cli:
            w(f"| `{name}` | {desc} |")
        w("")

    w("## Reading this")
    w("")
    w("- A message with no handler is **not callable**, however complete its "
      "schema looks.")
    w("- Requests are dispatched by fully-qualified proto name via "
      "`Any::ParseAnyTypeUrl()` matched against `RequestType().GetTypeName()` "
      "(`common/api/api_handler.cpp`), so the message name in this table is "
      "exactly what goes on the wire.")
    w("- See `03-gaps.md` for what is missing and what it blocks.")
    w("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed doc is out of date")
    ap.add_argument("-o", "--out", type=Path, default=DOC)
    args = ap.parse_args(argv)

    text = render()

    if args.check:
        if not args.out.is_file():
            print(f"{args.out} missing; regenerate", file=sys.stderr)
            return 1
        # Ignore the regeneration date line when comparing.
        strip = lambda s: [ln for ln in s.splitlines()
                           if not ln.startswith("> KiCad ")]
        if strip(args.out.read_text(encoding="utf-8")) != strip(text):
            print(f"{args.out} is out of date; regenerate", file=sys.stderr)
            return 1
        print(f"{args.out.name} up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
