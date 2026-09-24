#!/usr/bin/env python3
"""Report enum values a function that should be exhaustive over them misses.

Motivation, and why the obvious check does not work.

`SetCustomDesignRules` is fully handled, passes the API inventory, and yet three
`CustomRuleConstraintType` values were unreachable: `CRS_INVALID`, "Unsupported
custom rule constraint type". The first version of this tool asked "does this enum
value appear anywhere in the C++" and reported no gaps at all -- because
`api_pcb_enums.cpp` *does* map every value bidirectionally to its
`DRC_CONSTRAINT_T`. The value was referenced; it was simply absent from one
`switch` that had to cover it, `DRC_RULE::FormatRuleFromProto()`.

So the bug class is not "unimplemented enum value". It is "a function that must be
exhaustive over an enum is not", and the check has to be per-function.

    # which CustomRuleConstraintType values does FormatRuleFromProto miss?
    python documentation/tools/check_enum_coverage.py \\
        --enum CustomRuleConstraintType \\
        --function pcbnew/drc/drc_rule.cpp:FormatRuleFromProto

    # run the checks recorded below (CI-friendly; exits non-zero on a gap)
    python documentation/tools/check_enum_coverage.py --known
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROTO_ROOT = REPO / "api" / "proto"

ENUM_RE = re.compile(r"^enum\s+(\w+)\s*$", re.MULTILINE)
VALUE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*;", re.MULTILINE)

# Functions expected to cover an enum exhaustively, with values that are
# legitimately absent. Add to this as new mappings appear.
KNOWN_CHECKS: list[tuple[str, str, set[str]]] = [
    (
        "CustomRuleConstraintType",
        "pcbnew/drc/drc_rule.cpp:FormatRuleFromProto",
        {
            # Placeholder, never a real constraint.
            "CRCT_UNKNOWN",
            # Rule form is (constraint return_path (layer "B.Cu") (net "GND")):
            # a layer and a net, not a numeric value. CustomRuleConstraint's
            # oneof has no variant that can carry it, so this needs a proto
            # schema addition rather than another case. Remove from this set when
            # that lands, and the check will start demanding it.
            "CRCT_NET_CHAIN_RETURN_PATH",
        },
    ),
    (
        # The handler translates AR_RESULT into this enum. A value with no case
        # would surface as a caller reading APR_UNKNOWN for a run that had a
        # perfectly definite outcome.
        "AutoplaceResult",
        "pcbnew/api/api_handler_pcb.cpp:handleAutoplaceFootprints",
        set(),
    ),
]


def enum_values(name: str) -> list[str]:
    for path in sorted(PROTO_ROOT.rglob("*.proto")):
        text = path.read_text(encoding="utf-8")

        for m in ENUM_RE.finditer(text):
            if m.group(1) != name:
                continue

            start = text.index("{", m.end())
            depth, i = 0, start
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1

            return [v for v, _ in VALUE_RE.findall(text[start:i])]

    raise SystemExit(f"enum not found in api/proto: {name}")


def strip_comments(text: str) -> str:
    """Drop // and /* */ comments so a value named only in prose does not count."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def function_body(spec: str) -> str:
    """Extract one function body, given `relative/path.cpp:FunctionName`."""
    rel, _, func = spec.rpartition(":")
    path = REPO / rel

    if not path.is_file():
        raise SystemExit(f"no such file: {rel}")

    text = path.read_text(encoding="utf-8")
    m = re.search(rf"\b{re.escape(func)}\s*\(", text)

    if not m:
        raise SystemExit(f"{func} not found in {rel}")

    # Brace-match from the first { after the signature.
    start = text.index("{", m.end())
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1

    return strip_comments(text[start:i])


def check(enum: str, spec: str, allowed: set[str]) -> list[str]:
    values = enum_values(enum)
    body = function_body(spec)
    return [v for v in values if v not in allowed and not re.search(rf"\b{v}\b", body)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enum")
    ap.add_argument("--function", help="path/to/file.cpp:FunctionName")
    ap.add_argument("--allow", default="", help="comma-separated values to ignore")
    ap.add_argument("--known", action="store_true", help="run the recorded checks")
    args = ap.parse_args(argv)

    if args.known:
        checks = KNOWN_CHECKS
    elif args.enum and args.function:
        allowed = {s.strip() for s in args.allow.split(",") if s.strip()}
        checks = [(args.enum, args.function, allowed)]
    else:
        ap.error("give --enum and --function, or --known")
        return 2

    failures = 0

    for enum, spec, allowed in checks:
        missing = check(enum, spec, allowed)
        status = "OK" if not missing else f"{len(missing)} MISSING"
        print(f"{spec}  vs  {enum}: {status}")

        for v in missing:
            print(f"    {v}")

        if allowed:
            print(f"    (allowed: {', '.join(sorted(allowed))})")

        failures += bool(missing)

    if failures:
        print(
            "\nA missing value means the function has no case for it. For "
            "FormatRuleFromProto that surfaces as SetCustomDesignRules answering "
            "CRS_INVALID for a constraint DRC implements."
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
