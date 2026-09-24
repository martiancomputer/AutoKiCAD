# Status

## Objective

Inject standardized APIs into KiCad at multiple levels so AI agents can drive PCB
creation end to end: symbol selection, schematic capture, layout, routing,
layers/stackup, and engineering calculations.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Routing | Write a real autorouter on `PNS::NODE` | KiCad has no autorouter. `ROUTER_TOOL::RouteSelected` is greedy point-to-point with no rip-up-and-retry, and is GUI-bound. See [05-pns-router.md](05-pns-router.md). |
| Execution model | GUI-attached under Xvfb | Agent must be able to see intermediate state, not route blind. |
| Extend vs fork | Extend upstream's IPC API | Upstream already built the transport; a parallel stack forfeits it and upstreamability. KiCad is GPLv3, so keeping agent logic out-of-process also avoids derivative-work entanglement. |
| Agent-facing layer | External process, not in-tree C++ | Iterate without 40-minute rebuilds. |

### Note on the observability decision

The stated reason for Xvfb was so the agent can screenshot progress. That
requirement is right; screenshotting is the weakest way to satisfy it. Cheaper
and more precise channels, all headless:

- `RunBoardJobExportRender` — programmable camera (side, zoom, rotation,
  perspective, lighting, W×H). Deterministic across calls.
- `RunBoardJobExportSvg` with per-layer output — inspect exactly `F.Cu` with no
  overlay clutter. Better than a screenshot for routing.
- `PNS::DEBUG_DECORATOR` — named stages, iteration indices, colored shapes,
  replayable step-by-step in `qa/tools/pns`'s log viewer. Shows the router's
  *reasoning*, not just its result.
- Scalars: unrouted-net count and DRC violation lists. ~50 tokens instead of
  ~1500, and not subject to misreading a picture.

These are additive, not a replacement for the Xvfb runtime.

## Current state

### Done

- **Full KiCad master build** — `10.99.0`, 3397 targets, **0 compile errors** on
  GCC 16.1.1 / protobuf 35.1 / CMake 4.4.2. See [04-build.md](04-build.md).
- **`kicad-cli api-server` confirmed present** in the built binary. Absent from
  released 10.0.5, which is why the dev build was required.
- **PNS runs headlessly** — verified real shove/walkaround iteration through
  `PNS_LOG_PLAYER_KICAD_IFACE : PNS_KICAD_IFACE_BASE`, no GUI.
- **`autokicad` observe layer** — DRC → structured `Observation`, per-layer SVG
  and 3D render, agent-shaped summaries. 95 unit + 9 integration tests.
- **`autokicad.ipc` client** — envelope, token, `Any` packing, status→exception
  mapping, retry policy, socket discovery. Verified against a real protobuf
  runtime via a loopback server.
- **API inventory generator** — [01-api-inventory.md](01-api-inventory.md).

- **Live IPC verified (2026-08-09)** — `kicad-cli api-server` driven end to end
  from our client: `version()`, `open_boards()`, and 14 real nets read off a
  preloaded board. Reproducible via `test_ipc_live.py` (spawns a server, 8
  tests, ~6 s). Needs `KICAD_RUN_FROM_BUILD_DIR=1`; see
  [02-ipc-protocol.md](02-ipc-protocol.md).

- **DRC over IPC (2026-08-09)** — `RunDrc` message + `handleRunDrc`, built on a
  new `DRC_REPORT::BuildReport()` so the API and `kicad-cli pcb drc` share one
  implementation. Produces observations identical to the CLI path.

**Test totals: 115 passing, 2 skipped** (95 unit + 9 CLI integration + 8 live IPC
minus overlap), full suite ~6m46s.

### Not done

- **ERC is not in the API** — DRC landed 2026-08-09, so the board half of the
  loop is closed; the schematic half is not. See [03-gaps.md](03-gaps.md).
- **No autorouter.** First increment exists: shortest-first ordering and a
  correctly initialised `DRC_ENGINE`, which took DRC errors introduced across the
  8-board corpus from 29 to 0. Still missing rip-up-and-retry, layer/via
  changes, differential pairs, and constraint-driven cost — and on the two
  largest boards it now closes no connections at all.
- **Schematic side barely exposed**, and nothing mutating exists in our code.
- **Xvfb runtime works** — `documentation/tools/xvfb-gui.sh` runs pcbnew from
  the build tree on a headless display, with isolated config and the IPC server
  enabled. Used to prove the GUI can route `dp_test`'s differential pair.

## Callable API surface

329 messages declared across 16 `.proto` files; **94 have handlers**. Per surface:

| Surface | Commands | Reachable when |
|---|---|---|
| PCB APP | 42 | pcbnew is running |
| BOARD | 22 | a `.kicad_pcb` is open |
| SCHEMATIC | 19 | a `.kicad_sch` is open |
| COMMON | 14 | always |
| EDITOR (base) | 8 | inherited by board *and* schematic |
| FOOTPRINT | 6 | footprint editor |

A message in the schema without a handler is **not callable**, however complete
it looks.

## Corrections to earlier claims

Recorded because the wrong belief was instructive.

**"`qa/tools/pns` is a validated regression bench."** Wrong. Running it: 2 of 8
cases pass. `kicad_add_boost_test( qa_pns_regressions ... )` is **commented out**
at `qa/tools/pns/CMakeLists.txt:168`, so upstream CI never runs it and the data
desynced unnoticed — each `pns.log` records a board content hash that no longer
matches any board in `boards/` (`backspace1.kicad_pcb` hashes `AED668AC…`, its
log wants `C64720C8…`). The harness *mechanism* works; the corpus does not. Plan
on recording our own cases.

**"`ngspice` needs installing."** Wrong package name — Arch ships it as
`ngspice`, not `libngspice`, and it was already present.

**"Build will take 1.5–2.5 hours / produce 12–16 GB."** Took ~6 h wall clock
across two runs and produced **20 GB**. Extrapolating from early build rates
underestimates: the expensive translation units and the serialized link phase are
all at the end.

**"All 14 board export jobs might not be handled."** Wrong — all 14 are. Caught
by the generated inventory rather than by eyeballing.

## Next steps

1. ~~Start `api-server`, make the first real IPC call.~~ **Done 2026-08-09.**
2. ~~Implement **DRC over IPC**.~~ **Done 2026-08-09** — see
   [03-gaps.md](03-gaps.md). The board half of the agent loop is now closed.
3. **Constraint round-trip** — express a DDR2 constraint set via
   `SetCustomDesignRules` on a real board and confirm DRC evaluates it. Validates
   the constraint pipeline before any autorouter exists. See
   [06-ddr2-acceptance-test.md](06-ddr2-acceptance-test.md).
4. ~~**Headless PNS write-back**~~ — **done 2026-08-09**, `pns_autoroute_hello`.
5. Record our own PNS test cases against `qa/data/pcbnew/pns_regressions/boards/`.
6. Then the autorouter, instrumented through `DEBUG_DECORATOR` from day one.
