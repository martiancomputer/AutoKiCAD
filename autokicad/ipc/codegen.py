"""Generate Python protobuf bindings from KiCad's .proto files.

The bindings are generated rather than committed: they must match the protos in
the checked-out KiCad tree, and that tree moves. Run this after pulling KiCad,
or whenever `api/proto/**` changes.

    python -m autokicad.ipc.codegen              # writes autokicad/ipc/_proto/
    python -m autokicad.ipc.codegen --check      # non-zero if regeneration needed

`protoc` and the Python `protobuf` runtime must be the *same* major version, or
the generated code will refuse to import. KiCad's api/CMakeLists.txt only emits
C++ (.pb.cc/.pb.h), so nothing upstream produces these for us.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# autokicad/ipc/codegen.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
PROTO_ROOT = REPO_ROOT / "api" / "proto"
OUT_DIR = Path(__file__).resolve().parent / "_proto"


def proto_files() -> list[Path]:
    return sorted(PROTO_ROOT.rglob("*.proto"))


def generate(out_dir: Path = OUT_DIR, *, protoc: str = "protoc") -> list[Path]:
    exe = shutil.which(protoc)
    if exe is None:
        raise RuntimeError(
            f"{protoc!r} not found. Install the protobuf compiler "
            "(Arch: pacman -S protobuf)."
        )
    if not PROTO_ROOT.is_dir():
        raise RuntimeError(f"KiCad protos not found at {PROTO_ROOT}")

    sources = proto_files()
    if not sources:
        raise RuntimeError(f"no .proto files under {PROTO_ROOT}")

    out_dir.mkdir(parents=True, exist_ok=True)
    rel = [str(p.relative_to(PROTO_ROOT)) for p in sources]

    proc = subprocess.run(
        [exe, f"--proto_path={PROTO_ROOT}", f"--python_out={out_dir}", *rel],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"protoc failed:\n{proc.stderr.strip()}")

    # protoc emits plain directories; make them importable packages.
    for pkg_dir in {p.parent for p in out_dir.rglob("*_pb2.py")} | {out_dir}:
        init = pkg_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")

    return sorted(out_dir.rglob("*_pb2.py"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--protoc", default="protoc")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if bindings are missing or older than the protos",
    )
    args = ap.parse_args(argv)

    if args.check:
        existing = sorted(args.out.rglob("*_pb2.py"))
        if not existing:
            print("protobuf bindings missing; run: python -m autokicad.ipc.codegen",
                  file=sys.stderr)
            return 1
        newest_proto = max(p.stat().st_mtime for p in proto_files())
        oldest_binding = min(p.stat().st_mtime for p in existing)
        if newest_proto > oldest_binding:
            print("protos are newer than bindings; regenerate", file=sys.stderr)
            return 1
        print(f"{len(existing)} binding(s) up to date")
        return 0

    written = generate(args.out, protoc=args.protoc)
    print(f"generated {len(written)} module(s) into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
