# autokicad — the agent's observe step

Structured board feedback for an AI agent: run DRC, collapse the results into
something a model can read cheaply, and optionally render what the board looks
like. Works against **released KiCad** (tested on 10.0.5) — no dev build needed.

## Why this exists

An agent that mutates a PCB needs to know what it broke. Screenshots are a poor
channel: expensive in tokens, non-deterministic, and they show the *result*
rather than the *problem*. This gives machine-readable violations first, with
visuals as an opt-in extra.

## Usage

```bash
python -m autokicad.cli board.kicad_pcb
python -m autokicad.cli board.kicad_pcb --layers F.Cu,B.Cu --render -a out/
python -m autokicad.cli board.kicad_pcb --json
```

Exit codes: `0` clean, `1` errors or unrouted nets, `2` tool failure. Warnings
alone do not fail.

```python
from autokicad import CliBackend

obs = CliBackend().observe("board.kicad_pcb")
print(obs.to_agent_text())

if obs.unconnected_count:          # routing progress metric
    ...
for v in obs.errors:               # things that are actively wrong
    print(v.type, v.anchor)
```

## The metrics that matter

`counts()` returns a true **partition**, which the raw KiCad JSON does not:

- **`unconnected`** — nets not yet routed. This is "how far has routing gotten".
  Drive it to zero.
- **`errors`** — routed, but violating board rules (clearance, shorts, …). Also
  drive to zero, but it means something different.
- **`parity`** — the board disagrees with the schematic.
- **`warnings`** — advisory; do not block on these.

KiCad gives `unconnected_items` *and* `schematic_parity` findings
`severity: error`. Filtering by severity alone therefore counts the same finding
in two buckets. `errors`/`warnings` are scoped to `Category.VIOLATION`, so
`errors + warnings + unconnected + parity` never exceeds the real total.

Both double-counts were found by tests, not by inspection — see
`test_counts_partition_total`.

## Tests

```bash
python -m pytest autokicad/tests -m "not integration" -q   # 66 tests, ~0.5s, no KiCad needed
python -m pytest autokicad/tests -m integration -q         # 9 tests, ~2min, needs kicad-cli
python -m pytest autokicad/tests -q                        # all 75
```

Unit tests parse captured fixtures in `tests/fixtures/` (generated from real
`kicad-cli` output, not hand-written) and stub `subprocess.run`, so they need no
KiCad install. Integration tests skip automatically when `kicad-cli` is absent.

Notable guards:

- `test_drc_never_writes_to_the_board` / `test_board_file_is_not_modified` —
  `--save-board` must never be passed; verified by SHA-256 before/after.
- `test_clean_env_strips_appimage_vars` — the AppImage bug below.
- `test_render_failure_does_not_sink_observation` — a broken visual must not
  discard structured findings.
- `test_unconnected_detected_on_stripped_board` — strips all copper from a real
  demo board to exercise the routing metric, since every shipped demo is fully
  routed.

## Token efficiency

`to_agent_text()` groups by `(type, severity)` with counts and one exemplar. A
board with 129 identical footprint-mismatch warnings costs six lines instead of
129 entries — the video demo goes from 72 KB of JSON to a short summary.

## Design: swapping the backend later

`models.py` is transport-independent. `CliBackend` shells out to `kicad-cli`
today; an `IpcBackend` speaking protobuf/nng to `kicad-cli api-server` will
populate the same `Observation`. Depend on the `Backend` protocol, not the class,
and agent code won't change.

Note `api-server` exists only on KiCad **master** — released 10.0.5 has just
`fp, jobset, pcb, sch, sym, version`. That's why the CLI backend comes first.

## Gotcha: AppImage environment leakage

If the parent process is an AppImage (Claude Desktop sets
`APPDIR=/tmp/.mount_claudeXXXX`), KiCad honours `APPDIR` for path resolution and
fails with:

```
Failed to load shared library '/tmp/.mount_claudeXXXX/usr/bin/_pcbnew.kiface'
```

`backend.clean_env()` strips `APPDIR`, `APPIMAGE`, `OWD`, `ARGV0` from the child
environment. Every `kicad-cli` call goes through it. Don't remove that without
testing from an AppImage-hosted shell — this was a real failure, not a
hypothetical.

## Other notes

- `refill_zones=True` by default. Stale zone fills produce phantom unconnected
  items, which would corrupt the routing metric. The board on disk is never
  modified (we never pass `--save-board`).
- `--exit-code-violations` is deliberately not used: it makes "violations found"
  indistinguishable from "the tool failed". Violations come from the parsed
  report instead.
- `excluded` and `comment` are absent from KiCad's JSON unless a violation is
  actually excluded — parsed defensively.
- For routing inspection prefer `--layers F.Cu` (2D, per-layer) over `--render`
  (3D); one file per layer means no overlay clutter.

## Status

75 tests passing (66 unit + 9 integration). Validated against `video`
(129 warnings → 1 group), `microwave` (8 errors: `shorting_items`/`clearance`),
`ecc83-pp`, `StickHub`, `complex_hierarchy` (clean), and a synthetically
unrouted board (14 unconnected).

Not yet covered:

- **ERC** — the schematic side is untouched.
- **`IpcBackend`** — protocol defined, implementation waits on a KiCad master
  build (`api-server` does not exist in released 10.0.5).
- **Library/symbol search**, placement, and anything that *mutates* a board.
  This package only observes.
