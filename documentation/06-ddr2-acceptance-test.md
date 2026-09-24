# Acceptance test: wire DDR2 to a DDR2 controller

The project's success criterion. DDR2 cannot be faked — a board either meets its
timing budget or it does not — so this is a real test rather than a demo.

## Why it is a good criterion

It exercises every layer at once: part selection, schematic capture, netlist
transfer, placement, controlled-impedance stackup, differential pairs, matched
routing, and verification. A stack that can do DDR2 can do almost anything
simpler. A stack that fakes any layer fails visibly.

## The surprise: KiCad's constraint model is already DDR2-complete

This was the main finding when investigating. Every constraint DDR2 needs exists
**and is exposed over the API** via `SetCustomDesignRules` (a *handled* command),
as structured protobuf rather than rule text.

From `CustomRuleConstraintType` in `api/proto/board/board.proto:343`:

| Constraint | DDR2 use |
|---|---|
| `CRCT_LENGTH` (23) | absolute net length windows |
| `CRCT_SKEW` (24) | **byte-lane matching** — DQ to DQS |
| `CRCT_DIFF_PAIR_GAP` (25) | DQS±, CK± geometry |
| `CRCT_DIFF_PAIR_INTRA_SKEW` (27) | P/N skew within a pair |
| `CRCT_MAX_UNCOUPLED` (26) | how far a pair may run uncoupled |
| `CRCT_VIA_COUNT` (28) | bounding layer transitions per net |
| `CRCT_TRACK_WIDTH` (11) | controlled impedance |
| `CRCT_NET_CHAIN_LENGTH` (37) | branch/tree topology totals |
| `CRCT_NET_CHAIN_STUB_LENGTH` (38) | **stub matching for T-topology** addr/cmd |
| `CRCT_NET_CHAIN_RETURN_PATH` (39) | return-path continuity |
| `CRCT_TRACK_ANGLE` (33) | geometry hygiene |

And critically, `CustomRuleConstraintOption` (`board.proto:387`):

```protobuf
CRCO_SKEW_WITHIN_DIFF_PAIRS = 1;
CRCO_SPACE_DOMAIN           = 2;
CRCO_TIME_DOMAIN            = 3;   // <-- propagation delay, not physical length
```

`CustomRuleConstraint.numeric` is documented as *"nanometers, **picoseconds**, or
unitless depending on constraint type"*. So constraints can be expressed in the
**time domain**, which is what DDR2 actually requires: matching physical length
across layers with different propagation velocities is wrong, and KiCad knows it.

Supporting machinery, all present:

- `pcbnew/length_delay_calculation/` — delay computation with tuning profiles
- `ROUTER_IFACE::CalculateRoutedPathDelay` / `CalculateLengthForDelay` /
  `CalculateDelayForShapeLineChain` — PNS can reason in delay directly
- `PNS::CONSTRAINT::m_IsTimeDomain` (`pns_node.h:81`) — the router honours it
- `PNS::DIFF_PAIR_PLACER`, meander and skew placers, `pcb_tuning_pattern`
- `pcbnew/tools/diff_phase_skew_tool.cpp`
- `pcbnew/net_chain_bridging.*` with `NET_CHAIN_PARTITION` for topology analysis
- `common/transline_calculations/` — microstrip/stripline impedance for the stackup

**Verification is therefore already solvable.** DRC can check a DDR2 interface
today. What is missing is *generation*.

## What DDR2 changes about the autorouter — read before writing code

DDR2 inverts the routing problem, and this should shape the architecture from
day one.

A conventional autorouter answers *"find a path for net N."* DDR2 asks:

> Find ~70 paths whose **propagation delays** match within a budget, in defined
> groups, at controlled impedance, with bounded via counts, continuous return
> paths, and differential pairs held coupled.

That is **constrained optimization over a group of nets**, not per-net
pathfinding. Consequences:

1. **The unit of work is a group, not a net.** Byte lane 0 (DQ0–7, DQS0±, DM0) is
   routed and evaluated together. A router whose core loop is per-net will need
   rewriting.
2. **Length/delay matching cannot be a post-pass.** Meandering to match after
   routing only works if space was reserved during routing. Budget must inform
   the cost function.
3. **Via count is a first-class cost**, not an afterthought — each via adds delay
   discontinuity and consumes return-path integrity.
4. **Layer assignment is part of the problem.** Different layers have different
   velocities, so layer choice changes delay, which changes matching.
5. **Success is a budget, not a boolean.** The objective is "worst-case skew
   within lane ≤ X ps", which is what makes `Branch()`-based search valuable —
   you can evaluate a candidate group and discard it cheaply.

A maze router retrofitted with length matching will fail this test. Plan for
group-wise constrained routing from the start.

## What is still missing

| Gap | Effect on this test |
|---|---|
| No autorouter | Nothing generates routes. The core work. |
| No library/symbol search | Cannot select the SDRAM or controller parts. |
| No placement over API | Placement dominates DDR feasibility; `AR_AUTOPLACER` is headless-ready but unexposed. |
| No schematic commands beyond CRUD | Building a ~70-net interface by raw item CRUD is painful. |
| DDR domain layer | Smaller than first thought: `vme-wren` encodes byte lanes as netclasses (`DDR4_BYTE0..3`, `DDR4_CMD`) with a length window per (netclass, `fromTo()` segment). We reuse that encoding. |
| Stackup not driven from impedance targets | `transline_calculations` can compute it; nothing wires it to a stackup. |

Note that the **strip-and-reroute** form of this test needs none of the
library-search, placement or schematic rows: the board already has parts placed
and a netlist. That form requires only the autorouter and the domain layer.

## Reference material already in the tree

`demos/` contains real high-speed boards, which makes this gradeable rather than
subjective:

| Board | Nets | DDR mentions | DQS refs |
|---|---|---|---|
| `vme-wren.kicad_pcb` | 39,781 | 1,707 | **503** |
| `jetson-agx-thor-baseboard.kicad_pcb` | 31,674 | 18 | 0 |
| `RoyalBlue54L-Feather.kicad_pcb` | 1,880 | 11 | 9 |

`vme-wren` is a human-routed DDR interface, 67 MB. That enables an objective
acceptance test:

> **Strip the DDR nets from `vme-wren`, re-route them with our router, and require
> the result to satisfy the same constraint set as the original — then compare
> quality metrics against the human routing.**

This reuses the copper-stripping technique already validated for
`unconnected_count` testing (see `autokicad/tests/test_integration.py`), scaled
up. The human routing is the benchmark; the constraint set is the pass/fail.

## Milestone ladder

DDR2 is roughly test #10, not test #1. Ordered so each step is independently
verifiable:

1. ~~**First real IPC exchange**~~ — **done 2026-08-09.**
2. ~~**DRC over IPC**~~ — **done 2026-08-09.** `RunDrc` + handler.
3. ~~**Constraint round-trip**~~ — **done 2026-08-09.** A rule set modelled on
   `vme-wren.kicad_dru` (per-layer differential impedance, a byte-lane length
   window with a `fromTo()` pin-pair condition, an `intersectsArea()` clearance)
   survives `SetCustomDesignRules`/`GetCustomDesignRules` **verbatim**, DRC
   evaluates it and attributes violations to the rule by name, and clearing the
   rules reverts DRC exactly to baseline.

   Required a fix: `DRC_ENGINE` caches parsed rules from its last
   `InitEngine()`, which in a long-lived api-server session is board load. Rules
   written afterwards were stored to the `.kicad_dru` but silently ignored.
   `handleRunDrc` now re-inits before every run, as the DRC dialog does.
4. ~~**Headless PNS "hello world"**~~ — **done 2026-08-09.**
   `qa/tools/pns/autoroute_hello_main.cpp` loads a board, syncs a PNS world,
   routes ratsnest connections and writes the board back. On a stripped
   `ecc83-pp`: unconnected 14 -> 11, 6 items added, **0 DRC errors**.

   Required `HEADLESS_PNS_IFACE`, because `PNS_KICAD_IFACE_BASE::AddItem()` is an
   empty stub -- item creation lives in the GUI subclass and depends on state
   only it has (`m_fpOffsets`, `m_itemGroups`, `m_replacementMap`). So PNS
   *computed* headlessly all along, but nothing it produced could reach a BOARD.
   That write-back path is the actual prerequisite for an autorouter, and it now
   exists for segments, arcs and vias.
5. ~~**Routing regression net**~~ — **done 2026-08-09.** Baselines recorded for
   all 8 boards in `pns_regressions/boards/`; see `autokicad/routing.py`.

   The baselines characterise the naive router honestly: it closes 1–5
   connections per board and **introduces DRC errors on 7 of 8**
   (`diff_pair_gap_out_of_range`, `shorting_items`, `clearance`). Those come
   from forcing `FixRoute(..., aForceFinish=true)` and from having no notion of
   differential pairs — D+/D- get routed as independent singles. Driving
   `drc_introduced` to zero is the autorouter's first real objective.

6. **Autorouter — started 2026-08-09, far from done.**

   First increment landed: shortest-first net ordering, and — the real fix —
   creating and initialising the board's `DRC_ENGINE` before routing. PNS
   resolves *every* clearance through `m_DRCEngine->EvalRules()`, and
   `BOARD_LOADER` is what normally builds that engine. Loading a board straight
   through `PCB_IO` bypasses it, so the router had been placing copper with **no
   design rules at all**.

   Effect across the 8-board corpus: **DRC errors introduced 29 -> 0.**

   | Board | progress | drc introduced |
   |---|---|---|
   | backspace1 | 2 -> 1 | 3 -> 0 |
   | dp_test | 2 -> 1 | 3 -> 0 |
   | pic_programmer | 5 -> 5 | 4 -> 0 |
   | shove_same_net_via | 2 -> 2 | 0 -> 0 |
   | simple | 5 -> 5 | 5 -> 0 |
   | stickhub-extra-via | 5 -> 6 | 4 -> 0 |
   | ultrasound | 1 -> 0 | 3 -> 0 |
   | video-v10 | 1 -> 0 | 7 -> 0 |

   Read the trade honestly: legality was bought partly with completion. On the
   two largest boards the router now closes **nothing**, because it cannot find
   a legal path without the things it still lacks. What remains is the actual
   autorouter:

   - **rip-up-and-retry** on `NODE::Branch()` — nothing is ever reconsidered
   - **layer changes and via insertion** — everything is routed on one layer
   - **differential pairs** — D+/D- are routed as independent singles, via
     `LINE_PLACER` rather than `DIFF_PAIR_PLACER`
   - **constraint-driven cost** — length/skew budgets do not inform any decision
   - **`DEBUG_DECORATOR` instrumentation** — the search is currently opaque

7. ~~**Unconstrained bus**~~ — **done 2026-08-09.** 8 nets on `simple.kicad_pcb`:
   8 routed, 8 connections closed, 0 failures, 0 DRC errors introduced.

   Two capabilities got it there:

   * **Strategy retry** — walkaround then shove, across every enabled copper
     layer, instead of one attempt on one layer. This is what unblocked the two
     largest boards.
   * **Verify-and-rollback** — PNS reporting success is *not* the same as the
     connection being made. Layer alternatives happily produced tracks on a layer
     neither pad reaches: dead copper that raised the "routed" count while
     closing nothing. Each attempt is now accepted only if the unrouted count
     actually fell, and undone otherwise.

   | Board | progress | drc introduced |
   |---|---|---|
   | backspace1 | 1 -> 1 | 0 |
   | dp_test | 1 -> 1 | 0 |
   | pic_programmer | 5 -> 5 | 0 |
   | shove_same_net_via | 2 -> 2 | 0 |
   | simple | 5 -> 5 | 0 |
   | stickhub-extra-via | 6 -> 6 | 0 |
   | **ultrasound** | **0 -> 5** | 0 |
   | **video-v10** | **0 -> 5** | 0 |

   Scope note: this is rollback of *failed* attempts, not true rip-up-and-retry.
   Nothing already committed is ever reconsidered to make room for a later
   connection, and `NODE::Branch()` is still unused. Real rip-up remains open.

8. **Skew-constrained bus** — **done 2026-08-09**, with honest limits.

   *Grading.* `autokicad.routing.measure_skew()` routes a bus, scopes a `skew`
   rule to the nets that actually carry copper, and grades the result. Two things
   had to be right for the number to mean anything: scope the rule to routed nets
   (a netclass-wide rule sweeps in unrouted nets whose zero length reports a skew
   equal to the group maximum -- 5 real violations became 26), and parse both
   board formats (older KiCad writes one-line segments with a numeric `(net 1)`,
   master writes multi-line with `(net "name")`).

   *Tuning.* `pns_autoroute_hello --tune` drives `PNS_MODE_TUNE_SINGLE` /
   `MEANDER_PLACER` headlessly, lengthening every routed net toward the longest.
   **This is the first thing in the router where a constraint decides where
   copper goes rather than only grading it afterwards.**

   On a stripped `ecc83-pp` routing 6 connections, target 12.268 mm:

   | net | length | |
   |---|---|---|
   | 12 | 8.974 -> 12.400 mm | converged |
   | 5 | 10.444 -> 12.613 mm | converged |
   | 3 | 3.175 -> 4.351 mm | ran out of room |
   | 7 | 3.922 -> 3.922 mm | no room at all |

   Skew against a 1 mm budget: **4 violations -> 2, worst 9.093 -> 8.596 mm.**

   It does not reach zero, and the reason is structural rather than a bug: a net
   3 mm long cannot absorb 8 mm of meander in the space available. Closing that
   gap needs the router to reserve space for tuning *while* routing, which is
   consequence 2 in the section above -- length matching cannot be a pure
   post-pass. What exists now is the post-pass.

   Three setup requirements surfaced, all the same shape as earlier findings:

   * `BOARD::BuildListOfNets()` + `SynchronizeNetsAndNetClasses()` +
     `SynchronizeTuningProfileProperties()` -- `BOARD_LOADER` does these, `PCB_IO`
     does not.
   * A **project** must be loaded and attached with `SetProject()`: netclasses
     live in the project, and without one `GetEffectiveNetClass()` is null.
     Note the `pns_regressions/boards/` corpus ships **no project files at all**,
     so netclass-dependent features cannot run on it.
   * `MEANDER_SETTINGS::m_netClass` must be set explicitly. The delay calculation
     dereferences it without a null check
     (`tuning_profile_parameters_user_defined.cpp:42`), so leaving it unset is a
     segfault rather than a fallback.

9. **Differential pairs** — **PARKED 2026-08-09.** Wired and thoroughly
   diagnosed; still not closing connections. Resume from the hypothesis table
   below rather than starting over.

   `PNS_MODE_ROUTE_DIFF_PAIR` is attempted first for any net whose partner
   `BOARD::DpCoupledNet()` resolves, falling back to single-ended. Four fixes
   were needed before the placer would run at all:

   * **A real start item.** `DIFF_PAIR_PLACER` refuses without one ("Cannot start
     a differential pair in the middle of nowhere") where `LINE_PLACER` starts
     happily in empty space — a tolerance that had been hiding a latent bug: the
     positional `HitTest` at the ratsnest anchor was returning null all along.
     Now resolved with `NODE::FindItemByParent()`, since the ratsnest already
     knows which pad each anchor belongs to.
   * **A pair-aware end point**, aimed between both destination pads, with the
     raw anchor as fallback.
   * **Diff-pair geometry.** `SIZES_SETTINGS::SetDiffPairWidth()`/`SetDiffPairGap()`
     from the netclass. Unset, the pair cannot fit and the failure reads as
     "cannot route" rather than "not configured".
   * **Checking `Move()`'s return value**, which was being ignored.

   ### Diagnosis

   Instrumenting the failure through public API (`PLACEMENT_ALGO::Traces()` plus
   `NODE::CheckColliding()` on each trace) localised it precisely:

   ```
   diffpair walkaround on F.Cu: FixRoute failed; 2 trace(s), no collision:
       [seg=0 pts=0 len=0.000mm] [seg=0 pts=0 len=0.000mm]
   diffpair shove on F.Cu:      Move produced no trace
   ```

   So `m_fitOk` is **not** the blocker — there is no collision. The chain is:

   1. `Start()` resolves the primitive pair — succeeds.
   2. `routeHead()` → `FitGateways()` builds `m_currentTrace`.
   3. `rhWalkOnly()` then calls `tryWalkDp( node, m_currentTrace, false )`, which
      takes the trace **by reference**, returns `true`, and leaves it **empty**.
   4. `DIFF_PAIR_PLACER::FixRoute()` bails on its
      `CP().SegmentCount() < 1 || CN().SegmentCount() < 1` guard.

   Under `RM_Shove`, `Move()` returns false outright.

   ### Root cause: an upstream bug in tryWalkDp()

   `DIFF_PAIR_PLACER::tryWalkDp()` (`pns_diff_pair_placer.cpp:279`) tries four
   walk attempts and keeps the best-scoring one:

   ```cpp
   DIFF_PAIR best;
   double bestScore = 100000000000000.0;      // sentinel: nothing found yet

   for( int attempt = 0; attempt <= 3; attempt++ )
       if( attemptWalk( ... ) ) { ... bestScore = score; best = std::move( p ); }

   if( bestScore > 0.0 )                      // always true, even on total failure
   {
       aPair.SetShape( best );                // 'best' is default-constructed: EMPTY
       return true;                           // reports success
   }
   ```

   When all four attempts fail, `bestScore` remains at 1e14, `> 0.0` still holds,
   and the caller receives a **default-constructed (empty) `DIFF_PAIR` together
   with a success return**. Downstream, `m_fitOk` is set true and
   `FixRoute()` then rejects the route on its
   `CP()/CN() SegmentCount() < 1` guard — an unroutable pair presented as a fit.

   Fixed here by tracking whether a walk actually succeeded rather than testing
   the sentinel. Behaviour becomes honest: `Move()` now returns false instead of
   claiming success with no geometry. Upstream's own `qa_pns_regressions` shows
   the same 6 pre-existing data failures before and after, and our routing
   baselines are unchanged.

   **Worth reporting upstream.** It is a small, self-contained logic error, and
   it silently converts "cannot route this pair" into a confusing downstream
   rejection.

   ### Decisive test: the GUI *can* route this pair

   Run under Xvfb (`documentation/tools/xvfb-gui.sh`), with the stripped
   `dp_test` loaded: pressing `6` (Route Differential Pair) and clicking U1's
   pair produced **two coupled parallel traces running to J1**. So
   `DIFF_PAIR_PLACER` works on this exact board, and `attemptWalk()` failing
   headlessly is **our misuse, not an upstream limitation**.

   A second finding fell out of it. KiCad has **no automated diff-pair routing
   at all**:

   ```cpp
   TOOL_ACTION PCB_ACTIONS::routerAutorouteSelected( ...
           .Parameter( PNS::PNS_MODE_ROUTE_SINGLE ) );
   ```

   "Attempt Finish Selected (Autoroute)" is hard-wired to single-ended. Pairs are
   interactive-only, so there is no GUI code path to copy for batch diff-pair
   routing — we are writing something upstream does not have.

   ### Hypotheses tested and eliminated

   | # | Hypothesis | Verdict |
   |---|---|---|
   | 1 | `m_fitOk` false because the pair collides | **No** — instrumentation reports "2 trace(s), no collision" |
   | 2 | Needs incremental `Move()` like `ROUTER_TOOL` | **No** — 8-step walk gives identical results |
   | 3 | `tryWalkDp()` sentinel bug | **Real and fixed**, but not the blocker |
   | 4 | `ROUTING_SETTINGS( nullptr, "" )` lacks defaults | **No** — the constructor sets every default explicitly before registering params, so it already matches a fresh-config GUI |
   | 5 | Hand-rolled `SIZES_SETTINGS` instead of `ImportSizes()` | **No** — switched to the canonical path; still fails |

   Hypothesis 4 was settled by reading the constructor rather than building,
   which is worth repeating: a 30-second read beat a 10-minute build.

   Hypothesis 5 was still worth keeping. `ImportSizes()` resolves track width,
   via size and diff-pair width/gap from the netclass and design settings
   together, including the "use netclass values" indirection; hand-setting three
   fields produced a `SIZES_SETTINGS` that looked self-consistent but was missing
   everything else the placer reads. It is the correct call regardless of this
   bug, and combined with the `tryWalkDp()` fix the failure is now honest:
   `Move()` returns false in both walkaround and shove rather than claiming
   success with no geometry.

   ### Where it stands

   Still failing at `attemptWalk()` inside `DIFF_PAIR_PLACER`, with the GUI
   demonstrably able to route the same pair on the same board. The next
   difference worth testing is the **start item**: the GUI resolves it from a
   click that snaps to U1's pad pair, while the headless tool starts from a
   ratsnest anchor and passes `nullptr` as `ImportSizes()`'s start item. Beyond
   that, instrumenting `attemptWalk()` itself is the remaining option.

   Consequence for now: D+/D- are routed as independent singles, which is what
   DRC reports as `diff_pair_gap_out_of_range`.

10. **Branch/stub topology** — *constraints now expressible; routing not started.*

    Net chains are KiCad's model for one electrical signal spanning several net
    names — controller → series resistor → memory — which is exactly DDR
    address/command. They are defined in the schematic (`SCH_NETCHAIN`), carried
    through the netlist, **and stored in the board file directly**:

    ```
    (net_chains (net_chain (name "X") (members (net "A") (net "B"))
                           (terminal_pad "<uuid>")))
    ```

    So a test board can declare chains with no schematic tooling.

    ### Correction to an earlier claim in this document

    It previously stated that `net_chain_stub_length` and
    `net_chain_return_path` "can be expressed nowhere in text". **That was
    wrong.** The `.kicad_dru` keywords exist and the parser has always accepted
    them — they are simply *unprefixed*:

    | Constraint | `.kicad_dru` keyword | parser |
    |---|---|---|
    | `CRCT_NET_CHAIN_LENGTH` | `net_chain_length` | `drc_rule_parser.cpp:550` |
    | `CRCT_NET_CHAIN_STUB_LENGTH` | `stub_length` | `:551` |
    | `CRCT_NET_CHAIN_RETURN_PATH` | `return_path` | `:552` |

    A grep for `net_chain_stub_length` found nothing and I concluded the feature
    was absent. Verified by hand-writing a `.kicad_dru` using `stub_length`,
    which parses and runs.

    ### What was actually missing, and is now fixed

    Only the **proto → rule-text** direction. `DRC_RULE::FormatRuleFromProto()`
    mapped no `CRCT_NET_CHAIN_*` case, so `SetCustomDesignRules` rejected
    constraints DRC fully implements. Adding two cases fixes it:

    ```
    before                              after
    chain_length       CRS_INVALID      CRS_VALID
    chain_stub_length  CRS_INVALID      CRS_VALID
    chain_return_path  CRS_INVALID      CRS_INVALID  (see below)
    ```

    Round-trip verified: type 38, max 5 mm, preserved exactly.
    **T-topology stub matching is now expressible over the API.**

    ### Return path still needs a proto change

    `return_path`'s rule form is `(constraint return_path (layer "B.Cu")
    (net "GND"))` — a layer and a net, not a numeric value.
    `CustomRuleConstraint`'s `oneof` carries `numeric`, `disallow`,
    `zone_connection` and `assertion_expression`, none of which can hold that
    pair. Exposing it needs a new oneof variant, not another serialisation case.
    Pinned by `test_net_chain_return_path_still_unsupported`.

    Routing to a stub topology has not been attempted — that waits on the
    autorouter.

11. **Full byte lane** — DQ0-7 + DQS0± + DM0 as one group, meeting budget.
12. **Full DDR2 interface** — all lanes, addr/cmd, clocks, on a real stackup.

Steps 1-7 are done. The verification half is complete and the router now places
legal copper and closes connections on every board in the corpus. Steps 8-12 are
the generation half, and each needs a capability the router does not yet have.
