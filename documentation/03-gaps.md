# Gaps

What does not exist, what each gap blocks, and roughly what closing it costs.
Ordered by how much it blocks.

## 1. ERC is not in the API — and DRC now is

**DRC: closed 2026-08-09.** We added `RunDrc` / `RunDrcResponse` to
`api/proto/board/board_commands.proto` and `API_HANDLER_PCB::handleRunDrc`, built
on a new `DRC_REPORT::BuildReport()` so the API and
`kicad-cli pcb drc --format json` share one implementation and cannot drift.
Verified to produce observations identical to the CLI path -- same counts, types,
severities and coordinates.

The payoff is that IPC DRC sees the board **as it currently is in the editor**,
unsaved edits included. The CLI structurally cannot; it only reads the last saved
file.

**ERC: closed 2026-08-20.** `RunErc` / `RunErcResponse` in
`schematic_commands.proto` and `API_HANDLER_SCH::handleRunErc`, built on a new
`ERC_REPORT::BuildReport()` extracted from `WriteJsonReport()` so the API and
`kicad-cli sch erc --format json` share one implementation and cannot drift.

Verified identical to the CLI on a 46-violation schematic: same count, same type
breakdown (`pin_not_connected` x37, `endpoint_off_grid` x5, `power_pin_not_driven`
x2, `pin_not_driven`, `footprint_link_issues`) and same coordinates to four
decimal places. Same payoff as DRC: it sees the schematic as it currently is in
the editor, unsaved edits included.

Violations carry their sheet path, which DRC has no equivalent of -- ERC groups
per sheet, and on a hierarchical design the same check firing on two sheets is two
different problems.

### Trap: a schematic internal unit is not a nanometre

`common.types.Vector2` is int64 nanometres, and `FromUserUnit()` returns *internal*
units. The board handler stores that result straight into `x_nm`, which is correct
only because `PCB_IU_PER_MM = 1e6` makes a pcbnew IU exactly one nanometre.
`SCH_IU_PER_MM = 1e4`, so a schematic IU is **100 nm**, and copying the board code
reported every ERC coordinate 100x too small -- 0.01 mm where the CLI said
1.0428 mm. The conversion now goes IU -> mm -> nm explicitly. Pinned by
`test_erc_positions_are_nanometres`.

Separately worth knowing: ERC's own reported coordinates are small and do not
correspond to sheet position -- a pin at 150 mm reports around 1.04 mm. That is
upstream behaviour, identical in the CLI JSON, and is reproduced faithfully rather
than corrected here.

### Known limitation: library tables under api-server

`kicad-cli api-server <board>` does not enable the project's footprint library
table. Library-parity checks therefore report *"The footprint library 'X' is not
enabled in the current configuration"* for every footprint, where
`kicad-cli pcb drc` on the same board and binary reports none.

This is environmental rather than a defect in the handler -- every other check,
including all coordinates and severities, agrees exactly. Pinned by
`test_library_parity_differs_under_api_server`; if that test starts failing,
upstream has fixed it and the filter can go.

**Narrowed 2026-08-19, still not explained.** Investigated while building library
search, and two candidate causes were *eliminated*:

- Not a missing project table. `ListLibraries` reports the demo's project library
  `Footprints` with `scope=PROJECT`, `enabled=true`, `ok=true`, resolving to the
  right path. Project tables are already loaded under api-server; adding an
  explicit `LIBRARY_MANAGER::ProjectChanged()` call to
  `command_api_server.cpp` changed nothing and was reverted rather than committed
  unverified.
- Not a missing global table either, once the profile is seeded.

The remaining discrepancy is that the two paths disagree. `testFootprintLink`
(`cvpcb/cvpcb.cpp:42`) asks the *adapter*: `HasLibrary(name, false)` is true but
`HasLibrary(name, true)` is false, i.e. `LIB_DATA::row->Disabled()`. Reading the
*table* for the same library gives `Disabled() == false`. So the adapter's cached
row disagrees with the table row. Upstream's own comment right there --
"CLI lazy-loading of libraries should be more unified... nothing will have
triggered a load up to this point" -- suggests the CLI library-loading path is
known-fragile. Worth resuming from `LIB_DATA::row` lifetime.

## 2. No autorouter — the big one

`pcbnew/autorouter/` contains only `ar_autoplacer` (component *placement*) and
`spread_footprints`. There is no track autorouter.

`PCB_ACTIONS::routerAutorouteSelected` → `ROUTER_TOOL::RouteSelected`
(`pcbnew/router/router_tool.cpp:1866`) does exist, but it is:

- **greedy point-to-point** — walks selected pads, follows the ratsnest, no
  global ordering, no rip-up-and-retry;
- **hard-bound to the GUI** — its first lines fetch `PCB_EDIT_FRAME`,
  `VIEW_CONTROLS`, `frame->GetCanvas()`, and call `frame->PushTool()`.

**Blocks:** end-to-end board creation.

**Cost:** large — a multi-month subproject. But the primitives are unusually
good; see [05-pns-router.md](05-pns-router.md).

## 3. Schematic API is thin

`schematic_commands.proto` contains exactly **two** operations, both read-only:
`GetSchematicHierarchy` and `GetSchematicNetlist`. Compare
`board_commands.proto` at ~50.

The schematic handler also does **not** register `RunAction`, while the board
handler does — so the generic "fire any tool action by name" escape hatch is
unavailable on the schematic side.

Generic CRUD does work, because `API_HANDLER_SCH` inherits
`API_HANDLER_EDITOR`. And `s_allowedTypes`
(`eeschema/api/api_handler_sch.cpp:59`) already permits the types that matter:
`SCH_SYMBOL_T`, `SCH_LINE_T` (wires), all label variants, `SCH_SHEET_T`,
`SCH_GROUP_T`. Notably commented out: `SCH_MARKER_T`, `SCH_TABLE_T`.

**Blocks:** schematic capture beyond raw item placement — no annotation, no
symbol library integration, no ERC.

**Cost:** medium, and mostly additive.

### `PlaceSymbol` — added 2026-08-20

`PlaceSymbol` / `PlaceSymbolResponse` in `schematic_commands.proto`, handled by
`API_HANDLER_SCH::handlePlaceSymbol`. Give it a `LIB_ID`, a position, and
optionally a reference and value; it resolves the symbol from the configured
libraries and places it.

**Why a dedicated command rather than `CreateItems`.** `CreateItems` accepts
`SCH_SYMBOL_T`, so the obvious approach is to send a `SchematicSymbolInstance`
carrying just a `LIB_ID`. That does not work, and fails *silently* — `ISC_OK`
every time:

1. `SCH_SYMBOL::Deserialize` (`sch_symbol.cpp:349`) builds a **new empty**
   `LIB_SYMBOL` from the `LIB_ID` and fills only text fields. It never loads the
   library symbol, so you get a symbol with no body and no pins.
2. Resolving the library symbol there is still not enough. The `LIB_SYMBOL`'s draw
   items end up in *instance* space: a resistor placed at (100, 70) saved its pins
   at `(-100, 73.81)` where the library has `(0, 3.81)`. The body lives in unit 0
   and drew correctly; the pins live in unit 1 and landed off-sheet. Placing at the
   origin produced correct coordinates, which is what pinned it down.
3. The instance also needs its **sheet path** set. Without it the
   `SCH_SYMBOL_INSTANCE` is registered under an empty `KIID_PATH`, and the symbol
   is then unreachable via `GetItems`, has no reference, and shows no pins — three
   symptoms from one missing field.

`handlePlaceSymbol` sidesteps all of it by using the constructor the interactive
tool uses (`sch_drawing_tools.cpp:454`):
`SCH_SYMBOL( *libSymbol, libId, &sheetPath, unit, bodyStyle, pos, schematic() )`.
That gets the unit, transform and instance data right in one step, and the result
is byte-identical to a hand-placed symbol.

`PlaceSymbol` also takes a `transform`, so symbols can be rotated (0/90/180/270)
and mirrored, and a `unit`, so a specific section of a multi-unit part can be
placed — an LM324's unit 2 is pins 5, 6 and 7.

**Wires, labels and junctions need none of this** — `SchematicLine`,
`LocalLabel`/`GlobalLabel` and `Junction` go through plain `CreateItems` and
render correctly, because they carry no library reference and live directly in
sheet coordinates.

### `SearchSymbols` — added 2026-08-20

The counterpart to `SearchFootprints`, and the step before `PlaceSymbol`: an agent
has to *find* `ESP32-S3-WROOM-1` before it can place it. Returns
`LibraryIdentifier`, description, keywords, pin count, unit count and whether the
symbol is a power symbol, with a `power_only` filter.

**Results are ranked, and ranked before `max_results` truncates** — exact name,
then prefix, then substring, then a match on library/description/keywords only.
Without that, searching power symbols for `"GND"` returns `Earth` first, because
its keywords are `"global ground gnd"` while the symbol actually named `GND` sorts
fourth alphabetically. A caller taking `results[0]` then wires the board to a net
called Earth — which is exactly what happened here, confirmed by
`GetSchematicNetlist`, until ranking was added.

Terms are matched per field rather than against one concatenated string, so a
query cannot straddle a field boundary — the same fix the footprint search needed.

**Performance turned out better than expected.** There is no symbol equivalent of
`FOOTPRINT_LIST`'s on-disk cache, so the first note here warned that an unscoped
search would be slow. Measurement contradicted it: eeschema preloads libraries
asynchronously at startup, so 22,830 symbols across a full stock install plus a
user library search in **0.2 s**. `libraries` narrows the result set, not the time.

### Trap: the library preload is asynchronous, and losing that race is silent

`PreloadLibraries()` is dispatched with `std::async` and `PreloadDesignBlockLibraries()`
onto the thread pool, so both return immediately and the libraries finish loading
*after* the API starts answering. A client that connects promptly — which any
script does — can enumerate before they are ready.

Losing that race does not raise. `GetSymbols()` returns an empty vector, so
`SearchSymbols` reports `total_indexed: 0` for a perfectly good library, and
`PlaceSymbol` fails with *"no symbol Device:R in the configured libraries"*, which
reads like a bad LIB_ID rather than a timing problem. Both handlers now call
`adapter->BlockUntilLoaded()` first, the same way `cvpcb.cpp` does before it
enumerates. Caught by `test_schematic_live.py`, which connects as soon as the
server answers and so hits the race every run.

### Trap: the GUI answers `AS_NOT_READY` forever if any global library table is missing

Worth reading even if you never touch schematics, because it blocks **every** kind
of GUI automation.

`single_top.cpp` calls `SetReadyToReply()` at the very end of `OnPgmInit`. Before
it, `startWizard.CheckAndRun( frame )` runs the first-run wizard — and that wizard
is **modal**. While it is open, `OnPgmInit` has not returned, so the API server is
listening and answering `AS_NOT_READY` to everything, indefinitely. Nothing times
out; nothing appears in any log.

The wizard runs when any of three providers wants input, and the one that catches
people is `LIBRARY_MANAGER::InvalidGlobalTables()`, which checks **symbol,
footprint and design block** tables. KiCad ships templates for the first two in
`/usr/share/kicad/template` but **none for design blocks**, so a fresh settings
profile is always invalid however carefully the other two are seeded. Writing an
empty `(design_block_lib_table (version 7))` is enough.

The privacy provider needs `do_not_show_again.update_check_prompt` and
`.data_collection_prompt` set true in `kicad_common.json`; the settings provider
just needs that file to exist. `documentation/tools/live-gui.sh` seeds all of it.

Diagnosing this was harder than it should have been: `wxLogTrace` output is
swallowed in GUI mode, and `ptrace_scope` blocks attaching a debugger to a
`setsid`-detached process, so the answer came from bracketing `OnPgmInit` with
`fprintf` probes.

## 4. Library search — **exposed 2026-08-19**

Two messages, on two surfaces, because KiCad splits the work that way:

- **`ListLibraries`** on the COMMON surface (`base_commands.proto`,
  `API_HANDLER_COMMON::handleListLibraries`). Covers symbol, footprint and
  design-block tables. Needs no open document — it reads the library tables rather
  than loading anything — and reports nickname, description, plugin type, scope,
  enabled/visible/ok, and *both* the configured and resolved URI.
- **`SearchFootprints`** on the PCB surface, backed by `FOOTPRINT_LIST`, the same
  index KiCad's own footprint chooser uses. Returns `LibraryIdentifier`,
  description, keywords and unique pad count.

Measured on the stock libraries: **15,457 footprints across 156 libraries, 4.1 s
cold, 0.9 s warm** (KiCad persists an fp-info-cache). A library-scoped search is
under a second either way.

`match_all_terms` requires each whitespace-separated term separately. Without it
`"0603 resistor"` is one substring and matches nothing useful, which is the
difference between a usable search and an empty one.

### Symbols list but do not search yet

`ListLibraries` covers symbol libraries, but there is no `SearchSymbols`. Entry
enumeration is type-specific — `IO_BASE` exposes none, so it cannot live in
common — and the symbol side is `SYMBOL_LIBRARY_ADAPTER::GetSymbolNames()`, which
lives in eeschema and so belongs on `API_HANDLER_SCH`. That handler needs an open
schematic, which our fixtures do not yet create. It is a near-exact mirror of the
footprint work; see gap 3.

### Two environmental traps, both silent

Neither produces an error — you just get nothing, which reads like "no such part":

1. **A fresh settings profile has no global library tables.** KiCad seeds
   `fp-lib-table` / `sym-lib-table` from `/usr/share/kicad/template` on first GUI
   run; nothing in the CLI path does it. Our 10.99 profile had neither, while 10.0
   did — so the libraries looked missing purely because the build is a different
   major-version profile.
2. **The stock library path follows the build's install prefix.** Our build
   resolves `${KICAD10_FOOTPRINT_DIR}` to `/usr/local/share/kicad/footprints`,
   which does not exist; the distro's libraries are in `/usr/share/kicad`. Every
   row still lists fine — this is visible *only* in `resolved_uri`, which is why
   the API reports it separately from `uri`.

The `library_server` test fixture supplies both.

## 5. Placement — **exposed 2026-08-19**

`AutoplaceFootprints` / `AutoplaceFootprintsResponse` in
`api/proto/board/board_commands.proto`, handled by
`API_HANDLER_PCB::handleAutoplaceFootprints`. Wraps `AR_AUTOPLACER`, which
upstream exposes only as an interactive tool. The board is modified in place;
nothing is written to disk.

The request names footprints by KIID, or places every footprint when the list is
empty. The response reports the result and which footprints actually moved.

### Cut: ratsnest length before/after

The response was going to report total airline length either side of the run —
autoplacement is heuristic, so reporting the objective it minimises is the only
honest way to tell an improvement from a no-op. **It was removed, because the
number could not be made correct.**

After moving footprints with `UpdateItems` and then measuring, the length came back
**byte-identical** to the untouched board (`153528837` nm) across three completely
different arrangements, including one that clustered all 15 footprints into a 4×8 mm
grid. The board was definitely mutated: summing `FOOTPRINT::GetPosition()` inside
the handler showed the clustered coordinates.

Neither of the two documented ways to refresh it worked:

- `BOARD::BuildConnectivity()` — a *full* rebuild that deletes every `RN_NET`,
  re-runs `CN_CONNECTIVITY_ALGO::Build()` and calls `internalRecalculateRatsnest()`.
  It returned `true` and changed nothing.
- `MarkItemNetAsDirty()` + `Update()` per footprint, then `RecalculateRatsnest()` —
  the exact sequence `BOARD_COMMIT::Push` uses for modified items
  (`board_commit.cpp:512`). Also changed nothing.

`CN_EDGE::GetLength()` is computed live from anchor positions rather than cached
(`connectivity_algo.h:141`), so stale anchors are the remaining suspect, but that
was not run down. What *is* established: the measurement is live with respect to
commits — three consecutive placements gave `before(n) == after(n-1)` exactly — and
correct at load time, reporting 0 for the fully-routed demo board and 153.5 mm once
copper was stripped. It is specifically blind to in-session mutations that arrive
through another API call.

Reporting it anyway would have meant presenting a stale figure as the optimisation
objective — the same failure mode as the "vanity metric" and "absolute DRC count"
traps recorded elsewhere in this project. Verifying it independently (rebuilding the
ratsnest from pad positions over IPC) is blocked too: `GetItems` returns pads with
`net.code` of 0 for all of them.

Worth resuming if placement quality ever needs measuring; the API surface can take
the fields back without breaking anyone, since they never shipped.

### Correction: `AR_AUTOPLACER` was *not* headless-clean

An earlier version of this document claimed the placer was ready to call headlessly
because the view overlay is optional. **That was wrong, and it segfaulted.**

`drawPlacementRoutingMatrix()` dereferences `m_overlay` unconditionally
(`ar_autoplacer.cpp:794`), and both of its call sites — 884 and 910 — invoke it
unguarded, even though the `m_progressReporter` and `m_refreshCallback` checks
immediately around them *are* guarded. So the overlay is the one optional-looking
member that is not actually optional. Setting it is not optional either: it is only
ever set from `AUTOPLACE_TOOL`, which has a view.

Headless, this crashes the api-server mid-handler. Over IPC that surfaces as a bare
socket timeout with an empty server log — the same misleading signature an escaped
exception produced in the transmission-line work, and worth recognising: **a
timeout with no reply usually means the server died, not that it is slow.**
`coredumpctl` had the answer immediately.

Fixed by an early return in `drawPlacementRoutingMatrix()` when there is no
overlay, which covers both call sites. This is a latent upstream bug — like the
`tryWalkDp()` sentinel in [05-pns-router.md](05-pns-router.md), it is invisible
from the GUI, where an overlay always exists.

The general lesson stands and is stronger than first written: *"constructor takes
no frame"* does not mean headless-safe. It has to be executed headless to know.

### Trap: the algorithm class is not the whole behaviour

Two preconditions also live in `AUTOPLACE_TOOL` rather than in the placer, and
calling the placer directly silently loses both:

1. **No board outline → bare `AR_FAILURE`.** `genPlacementRoutingMatrix()` returns
   0 when `GetBoardEdgesBoundingBox()` is degenerate
   (`ar_autoplacer.cpp:88`) and nothing says why. `AUTOPLACE_TOOL` checks first and
   names the layer (`autoplace_tool.cpp:62`); the handler does the same, so the
   caller gets "board edges must be defined on the Edge.Cuts layer" instead of an
   unexplained failure.
2. **`AR_AUTOPLACER` has no lock awareness whatsoever** — `IsLocked` does not
   appear anywhere in it. `AUTOPLACE_TOOL` strips locked footprints before handing
   them over, gated on `GetOverrideLocks()` (`autoplace_tool.cpp:74`). Without that
   filter in the handler, a locked footprint would be moved silently over the API.
   The handler filters by default, reports what it declined in `skipped_locked`,
   and takes `override_locks` for callers that mean it.

This is the same shape as the `PCB_IO` finding in
[05-pns-router.md](05-pns-router.md): a class that *looks* self-contained is only
half the behaviour, because the GUI wrapper carries preconditions the algorithm
assumes someone else checked. Worth assuming for every other tool we expose.

## 6. Calculations — **exposed 2026-08-09**

`RunTransmissionLineCalculation` on the COMMON surface (no document needed)
covers all eight geometries in `common/transline_calculations/`: microstrip,
coupled microstrip, stripline, coupled stripline, coplanar, coax, twisted pair
and rectangular waveguide.

Two modes. `TLM_ANALYSE` reports impedance, effective permittivity, propagation
delay and losses for a geometry. `TLM_SYNTHESISE` inverts it: given a target
impedance it solves for a geometry parameter — which is what impedance-controlled
routing needs to pick a trace width for a stackup.

Verified: 50 Ω on 1.6 mm FR-4 synthesises to **2.97 mm**, the standard answer, and
analysing that width returns 50.000 Ω.

### Trap: every declared parameter is zero-initialised

`TRANSLINE_CALCULATION_BASE` fills its map with zeros, so an omitted input is not
an error — it is a *wrong answer*. Leaving `TLP_TOP_HEIGHT` unset models an
enclosure lid crushed onto the trace, which produced **negative impedance** and an
`eps_eff` that fell as the trace widened. KiCad's UI defaults that parameter to
`1e20`, meaning "no cover". Callers must supply the full parameter set;
`test_microstrip_impedance_falls_with_width` asserts the monotonic behaviour that
catches this class of mistake.

Parameters a geometry does not model are rejected with a clear message rather
than throwing: `.at()` on the parameter map raises, and unguarded that escapes
the handler so the caller sees only a socket timeout.

## 7. PNS regression corpus is broken and disabled

`kicad_add_boost_test( qa_pns_regressions ... )` is **commented out** at
`qa/tools/pns/CMakeLists.txt:168`, so it is not a registered ctest and upstream
CI never runs it. Consequently the data rotted: of 8 enumerated cases, **2 pass**.

The 6 failures are hash-resolution failures. Each `pns.log` records a board
content hash; `PNS_LOG_FILE::Load()` only falls back to `<case>.dump` when that
hash matches nothing, and those dumps were never committed. Example:
`backspace1.kicad_pcb` hashes `AED668AC…` while its log wants `C64720C8…`.

**Blocks:** nothing structurally — the harness mechanism works, and the boards in
`boards/` are usable. But it means there is **no inherited safety net** for
autorouter work.

**Addressed 2026-08-09** by recording our own baselines instead of reviving
upstream's. `autokicad/routing.py` strips copper from each of the 8 boards, routes
headlessly, and records connections closed plus DRC errors *introduced*;
`test_routing_regression.py` (36 tests, ~64 s) asserts one-sidedly — progress may
improve, DRC may get cleaner, neither may regress — and fails loudly asking for a
re-record when a run beats its baseline.

Upstream's cases replay recorded *interactive* sessions, which is not what we are
building. Ours pin the property that matters: given a stripped board, does
headless routing still connect nets without adding violations.

## 8. Net-chain return-path constraint is not settable

**Mostly closed 2026-08-09.** `CRCT_NET_CHAIN_LENGTH` and
`CRCT_NET_CHAIN_STUB_LENGTH` now serialise through
`DRC_RULE::FormatRuleFromProto()` and round-trip over `SetCustomDesignRules`,
which unblocks T-topology address/command matching.

`CRCT_NET_CHAIN_RETURN_PATH` remains unsettable. Its rule form is
`(constraint return_path (layer "B.Cu") (net "GND"))` — a layer and a net rather
than a numeric value — and `CustomRuleConstraint`'s `oneof` has no variant that
can carry it. Needs a proto schema addition.

Note the `.kicad_dru` keywords for all three exist and always did
(`net_chain_length`, `stub_length`, `return_path`); only the proto direction was
missing. An earlier version of this document claimed otherwise.

**Cost:** small, but it is a schema change rather than a serialisation case.

## Non-gaps

Things that turned out to be present, listed so nobody re-investigates:

- **All 14 board export jobs are handled** — 3D, render, SVG, DXF, PDF, PS,
  Gerbers, drill, position, GenCAD, IPC-2581, IPC-D-356, ODB++, stats.
- **`ImportNetlist`** exists, so schematic → board transfer is available.
- **`RefillZones`** exists.
- **Design rules** are readable *and* writable, including custom rules
  (`GetCustomDesignRules` / `SetCustomDesignRules`).
- **Connectivity queries** exist: `GetNets`, `GetItemsByNet`,
  `GetItemsByNetClass`, `GetConnectedItems`, `GetNetClassForNets`.
- **`RunAction`** exists on the board handler — the universal escape hatch for
  anything tool-driven, though it returns no structured result.
- **Headless contexts** exist and carry their own `TOOL_MANAGER`.

## Suggested order

1. ~~**DRC over IPC**~~ — done 2026-08-09.
2. ~~**Calculations**~~ — done 2026-08-09.
3. ~~**Placement**~~ — done 2026-08-19.
4. ~~**Library search**~~ — done 2026-08-19 (footprints; symbols list but do not search).
5. **Schematic commands + ERC** — larger, additive. Carries `SearchSymbols` with it.
6. **Autorouter** — the long one; start recording PNS cases early.
