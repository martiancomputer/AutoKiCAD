# PNS internals for an autorouter

Notes for building a real autorouter on KiCad's P&S engine. PNS is a
self-contained subsystem with its own item model, bridged to KiCad by
`pns_kicad_iface.cpp`.

## Why PNS is the right foundation

Its core is DRC-aware, frame-free, and — crucially — has a **persistent,
copy-on-write world model**. That last property is what makes search tractable.

### `NODE::Branch()` — the search primitive

```cpp
/**
 * Create a lightweight copy (called branch) of self that tracks the changes
 * (added/removed items) wrs to the root.
 */
NODE* Branch();
void  Commit( NODE* aNode );
```

A branch is an overlay recording adds/removes relative to its parent, not a deep
copy. So you can speculatively route on a branch, evaluate the result, and
**discard it for free** — which is exactly the primitive rip-up-and-retry,
backtracking, and beam search all need. `Depth()` reports position in the
inheritance chain; `KillChildren()` discards descendants.

Caveat from the header: *"If there are any branches in use, their parents must
**not** be deleted."*

### Everything else you need is already there

| Capability | API |
|---|---|
| Obstacle queries | `QueryColliding`, `NearestObstacle`, `CheckColliding`, `HitTest` |
| Connectivity graph | `FindJoint`, `QueryJoints`, `FindLinesBetweenJoints`, `AssembleLine` |
| DRC constraints | `SetRuleResolver( RULE_RESOLVER* )` |
| Bulk world load | `BeginBulkAdd()` / `FinalizeBulkAdd()` |
| Mutation | `Add`/`Remove`/`Replace` for `SEGMENT`, `ARC`, `VIA`, `SOLID`, `LINE` |

`SetRuleResolver` means routes are **clearance-correct by construction** — you do
not post-hoc DRC your own output. `CONSTRAINT_TYPE` covers clearance, diff-pair
gap and skew, length, width, via diameter/hole, hole-to-hole, edge clearance, and
physical clearances.

`NearestObstacle` "follows the line in search of an obstacle nearest to the
line's starting point" — a ray-cast against the world, which is the inner loop of
most routing heuristics.

### Reusable algorithms, not just data structures

These are callable classes, not code buried in a tool:

- `PNS::SHOVE` — push-and-shove
- `PNS::WALKAROUND` — obstacle avoidance
- `PNS::OPTIMIZER` — post-route cleanup
- `PNS::TOPOLOGY` — topological queries
- `PNS::LINE_PLACER` — interactive line placement (implements `PLACEMENT_ALGO`)
- `PNS::DIFF_PAIR_PLACER`, meander/skew placers for tuning

An autorouter can use `SHOVE` and `WALKAROUND` as subroutines rather than
reimplementing them.

## The GUI seam

Already cut, which is the good news:

- **`PNS_KICAD_IFACE_BASE`** (`pcbnew/router/pns_kicad_iface.h:58`) — frame-free
  `ROUTER_IFACE` implementation.
- **`PNS_KICAD_IFACE`** (line 147) — GUI subclass; adds `SetView()` and the
  view/canvas concerns.

`ROUTER_IFACE` is a wide interface (~35 virtuals) covering world sync, item
display, net names, layer mapping, stackup height, and length/delay calculation.
The display-oriented ones (`DisplayItem`, `DisplayRatline`, `EraseView`,
`HideItem`) are no-ops in a headless implementation — `EraseView()` is already
`{}` in the base.

**What is GUI-bound is `ROUTER_TOOL`, not PNS.** `ROUTER_TOOL::RouteSelected`
(`router_tool.cpp:1866`) opens with:

```cpp
PCB_EDIT_FRAME*  frame = getEditFrame<PCB_EDIT_FRAME>();
VIEW_CONTROLS*   controls = getViewControls();
...
frame->PushTool( aEvent );
controls->ShowCursor( true );
frame->GetCanvas()->SetCurrentCursor( KICURSOR::PENCIL );
```

So an autorouter should be written against `PNS::NODE` and the algorithm classes
directly, **not** by trying to drive `ROUTER_TOOL` headlessly.

## What "autoroute" already does — and does not

`PCB_ACTIONS::routerAutorouteSelected` → `ROUTER_TOOL::RouteSelected` walks the
selected pads, pulls `RN_NET` ratsnest anchors from `CONNECTIVITY_DATA`, and
drives the line placer point-to-point, grouping successful routes into one undo
commit.

It is **not** an autorouter in any real sense: no global net ordering, no
rip-up-and-retry, no layer assignment strategy, no failure recovery beyond
skipping. Useful as a reference for how to drive the placer; not as a starting
implementation.

## Instrumentation: `DEBUG_DECORATOR`

`pcbnew/router/pns_debug_decorator.h` is a structured tracing interface exposed
via `ROUTER_IFACE::GetDebugDecorator()`:

```cpp
virtual void SetIteration( int iter );
virtual void NewStage( const wxString& name, int iter, ... );
virtual void BeginGroup( const wxString& name, int aLevel = 0, ... );
virtual void EndGroup( ... );
virtual void AddPoint( const VECTOR2I&, const COLOR4D&, int size, ... );
virtual void AddItem( const ITEM*, const COLOR4D&, ... );
virtual void AddShape( const SHAPE*, const COLOR4D&, ... );   // also SEG, BOX2I, LINE_CHAIN
virtual void Message( const wxString& msg, ... );
```

Named stages, iteration indices, nested groups, coloured geometry, source
locations. `qa/tools/pns`'s log viewer replays it **stage by stage**.

This is the answer to "how far has the router gotten" — it shows the search's
*reasoning*, not just its output. Instrument from day one; retrofitting
observability into a search algorithm is miserable.

## The bench

`qa/tools/pns/` builds with `-DKICAD_BUILD_PNS_DEBUG_TOOL=ON`:

- `qa_pns_regressions` — Boost.Test runner over recorded sessions
- `pns_debug_tool` — interactive log viewer / playground
- `PNS_LOG_PLAYER_KICAD_IFACE : PNS_KICAD_IFACE_BASE` — a **working non-GUI
  `ROUTER_IFACE`**, i.e. the reference implementation to copy

**Verified**: PNS executes shove/walkaround headlessly through this path, with
live iteration output (`check-back cc 19 items 0 coll 0`, `event [18/18]: move …`).

### But the corpus is broken — read this before relying on it

`kicad_add_boost_test( qa_pns_regressions ... )` is **commented out** at
`qa/tools/pns/CMakeLists.txt:168`. It is not a registered ctest; upstream CI has
never run it. The data has rotted accordingly: **2 of 8 cases pass**.

Mechanism of failure: `qa_pns_regressions_main.cpp` scans for `*.log`, reads a
board content hash via `GetLogBoardHash()`, and matches it against boards in
`pns_regressions/boards/`. `PNS_LOG_FILE::Load()` only falls back to
`<case>.dump` when that match fails — and those dumps were never committed, hence
`IO_ERROR: Unable to open .../pns.dump`.

| Case | Result |
|---|---|
| `issue24132-shove-same-net-via` | passes — hash matches `shove_same_net_via.kicad_pcb` |
| `walk-with-teardrops` | passes — ships its own `pns-no-hug-2.dump` |
| other 6 | fail — hash matches nothing |

Example: `backspace1.kicad_pcb` hashes `AED668AC…`; its log wants `C64720C8…`.
The board exists but was modified after the log was recorded.

**Implication:** we inherit a working *harness* but no working *corpus*. Plan to
record our own cases. The 8 boards in `boards/` are usable material —
`simple.kicad_pcb`, `dp_test.kicad_pcb`, `pic_programmer.kicad_pcb`,
`ultrasound.kicad_pcb`, `video-v10.kicad_pcb`, `stickhub-extra-via.kicad_pcb`,
`backspace1.kicad_pcb`, `shove_same_net_via.kicad_pcb`.

## Suggested approach

1. Copy `PNS_LOG_PLAYER_KICAD_IFACE` into a headless `AUTOROUTER_IFACE`.
2. Load a board, `SyncWorld()` into a root `NODE`, wire up the real
   `RULE_RESOLVER` so clearances come from the board's design rules.
3. Get net ordering and a cost function in place before any cleverness — ordering
   dominates quality in most routers.
4. Route on `Branch()`; `Commit()` on success, discard on failure. That is
   rip-up-and-retry.
5. Use `SHOVE`/`WALKAROUND` as subroutines; `OPTIMIZER` at the end.
6. Instrument every stage through `DEBUG_DECORATOR`.
7. Measure with unrouted-net count (from `CONNECTIVITY_DATA`) plus DRC violations
   — see `autokicad`'s `Observation.unconnected_count`.
