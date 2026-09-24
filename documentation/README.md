# AutoKiCAD documentation

Working record for injecting agent-facing automation into KiCad. Kept in-tree so
it is versioned alongside the code it describes.

| Doc | What it covers |
|---|---|
| [00-status.md](00-status.md) | Objective, decisions made, current state. **Start here.** |
| [01-api-inventory.md](01-api-inventory.md) | Every API message and whether it is actually callable. *Generated.* |
| [02-ipc-protocol.md](02-ipc-protocol.md) | The IPC wire protocol, verified against the C++ |
| [03-gaps.md](03-gaps.md) | What does not exist yet, and what each gap blocks |
| [04-build.md](04-build.md) | Building on Arch, and the traps we hit |
| [05-pns-router.md](05-pns-router.md) | PNS internals relevant to writing an autorouter |
| [06-ddr2-acceptance-test.md](06-ddr2-acceptance-test.md) | The success criterion: DDR2 ↔ controller. Milestone ladder. |

## Conventions

**Claims are sourced.** Anything asserted about KiCad's behaviour cites the file
(and line where useful) it came from. If something was inferred rather than
verified, it says so. This matters because several early assumptions in this
project turned out to be wrong, and undocumented guesses cost real time.

**Generated docs are never hand-edited.** `01-api-inventory.md` is produced by
`tools/gen_api_inventory.py` from `api/proto/**` plus the C++ handler
registrations. Regenerate after pulling KiCad:

```bash
python documentation/tools/gen_api_inventory.py
python documentation/tools/gen_api_inventory.py --check   # CI: fails when stale
```

**Two coverage checks, at different levels.** The inventory answers "does this
*message* have a handler". That is not sufficient: `SetCustomDesignRules` is fully
handled and still rejected three `CustomRuleConstraintType` values because one
`switch` was not exhaustive. `tools/check_enum_coverage.py` closes that:

```bash
python documentation/tools/check_enum_coverage.py --known   # CI: fails on a gap
```

Values that are legitimately absent are recorded in `KNOWN_CHECKS` with the
reason, so removing an entry turns the check back on.

**Other tooling.** `tools/xvfb-gui.sh` runs a KiCad GUI from the build tree on a
headless display, with isolated config and the IPC server enabled.

**Corrections stay visible.** When we get something wrong, the doc records the
correction rather than quietly overwriting it — the wrong belief is usually more
instructive than the right one.

## Layout of our code

```
autokicad/              agent-facing Python package
  models.py             transport-independent domain model (Observation, Violation)
  backend.py            CliBackend - shells out to kicad-cli
  cli.py                python -m autokicad.cli <board>
  ipc/                  protobuf-over-nng client for the KiCad IPC API
  tests/                95 unit + 9 integration
documentation/          this directory
build/                  out-of-tree build (gitignored)
```

Everything upstream-KiCad is untouched. Our code lives in `autokicad/` and
`documentation/` so rebases onto KiCad master stay clean.
