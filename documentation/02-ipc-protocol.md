# The KiCad IPC protocol

How to talk to a running KiCad. Every claim here was read out of the C++, not
inferred from documentation.

## Why this exists at all

Commit `65a442b1d2` (2026-03-22) — *"REMOVED: SWIG, wxPython, and Python
integration"* — deleted embedded scripting from KiCad. There are no `.i` files
left in the tree and `import pcbnew` does not exist on master. Automation is
**out-of-process only**, by design.

Three consequences worth internalising:

1. There is no in-process fast path. Everything crosses a socket.
2. Plugins are separate processes; `PYTHON_MANAGER` only locates and launches an
   external interpreter.
3. Because clients are at arm's length, an agent layer written against the API
   is not a derivative work of GPLv3 KiCad in the way an in-tree patch is.

## Transport

| Aspect | Value | Source |
|---|---|---|
| Library | nng (nanomsg-next-gen) | `libs/kinng/` |
| Socket type | **REP0** server → clients must use **REQ0** | `libs/kinng/src/kinng.cpp` (`nng_rep0_open`) |
| URL scheme | `ipc://` (Unix domain socket) | `common/api/api_server.cpp:142` |
| Default path | `/tmp/kicad/api.sock` | `common/api/api_server.cpp:87-92` |
| Collision fallback | `api-<pid>.sock` | `common/api/api_server.cpp:131` |
| Stale-socket handling | `api.lock` lockfile; orphaned sockets unlinked | `common/api/api_server.cpp:114-124` |

Non-abstract sockets are used deliberately — macOS and some platforms do not
support abstract sockets — which is why stale files must be cleaned up.

REQ0/REP0 is a **strict alternating request/reply state machine**. One outstanding
request per socket; give each thread its own.

### Multiple instances

Because a second KiCad falls back to `api-<pid>.sock`, a box running several
instances has several sockets and `/tmp/kicad/api.sock` belongs to whichever
started first. Anything that cares which KiCad it is driving must pass an
explicit URL. `autokicad.ipc.discover_socket_urls()` enumerates candidates for
diagnostics.

## Envelope

From `api/proto/common/envelope.proto`:

```protobuf
message ApiRequest {
  ApiRequestHeader header = 1;      // { kicad_token, client_name }
  google.protobuf.Any message = 2;  // the actual command
}

message ApiResponse {
  ApiResponseHeader header = 1;     // { kicad_token }
  ApiResponseStatus status = 2;     // { ApiStatusCode, error_message }
  google.protobuf.Any message = 3;  // the actual response
}
```

Every exchange is one `ApiRequest` → one `ApiResponse`. The command itself is
wrapped in an `Any`.

## Dispatch

This is the part worth getting right, because it determines wire compatibility.

`common/api/api_handler.cpp:43` calls
`google::protobuf::Any::ParseAnyTypeUrl( aMsg.message().type_url(), &typeName )`,
and `include/api/api_handler.h:96` registers handlers keyed by
`RequestType().GetTypeName()`.

So dispatch is by **fully-qualified protobuf message name**, e.g.
`kiapi.common.commands.Ping`. Python's stock `Any.Pack()` emits
`type.googleapis.com/kiapi.common.commands.Ping`, which `ParseAnyTypeUrl` strips
to exactly that name — **no custom type-URL handling is needed**. Verified by
`autokicad/tests/test_ipc.py::test_any_type_url_matches_kicad_dispatch`.

Practical upshot: the message names in [01-api-inventory.md](01-api-inventory.md)
are literally what goes on the wire.

## Status codes

`ApiStatusCode`, and how a client should treat each:

| Code | Meaning | Retry? |
|---|---|---|
| `AS_OK` = 1 | Succeeded | — |
| `AS_TIMEOUT` = 2 | Timed out | caller's call |
| `AS_BAD_REQUEST` = 3 | Invalid or illegal parameters | **no** |
| `AS_NOT_READY` = 4 | KiCad started recently, not ready | **yes** |
| `AS_UNHANDLED` = 5 | Not handled by KiCad | no |
| `AS_TOKEN_MISMATCH` = 6 | Wrong `kicad_token` | no |
| `AS_BUSY` = 7 | Mid-operation, cannot accept commands | **yes** |
| `AS_UNIMPLEMENTED` = 8 | In the schema, no implementation | no |

Only `AS_NOT_READY` and `AS_BUSY` are transient. An agent driving a live editor
hits both routinely — the user moves the mouse, a dialog opens — so retry with
backoff on those two and fail fast on everything else. Retrying
`AS_BAD_REQUEST` just burns time on a request that can never succeed.

`AS_UNIMPLEMENTED` deserves attention: it means the schema advertises something
KiCad will not do. Treat the schema as a superset of reality — hence the
generated inventory.

## Credentials

`common/api/api_plugin_manager.cpp:391` shows what KiCad passes to plugins it
launches:

```cpp
env.env["KICAD_API_SOCKET"] = Pgm().GetApiServer().SocketPath();
env.env["KICAD_API_TOKEN"]  = Pgm().GetApiServer().Token();
```

A client should honour both. `autokicad.ipc` defaults `token` from
`KICAD_API_TOKEN` and the URL from `KICAD_API_SOCKET`. A standalone client
against `kicad-cli api-server` may not need a token; if one is required and
missing, the answer is `AS_TOKEN_MISMATCH` rather than a silent failure.

## Headless operation

`kicad-cli api-server` (`kicad/cli/command_api_server.cpp`) runs the server with
no GUI: *"Run the KiCad IPC API server in headless mode"*, optionally
pre-loading a `.kicad_pro`, `.kicad_pcb` or `.kicad_sch`, with `--socket` to
override the path.

It backs onto `HEADLESS_PCB_CONTEXT` / `HEADLESS_SCH_CONTEXT`, which return
`CanAcceptApiCommands() == true` and **carry their own `TOOL_MANAGER`** — so
tool-driven operations are theoretically reachable without a frame.

**`api-server` is master-only.** Released 10.0.5 offers only
`fp, jobset, pcb, sch, sym, version`. Confirmed by running both binaries.

## Client implementation notes

Two traps we hit, both now regression-tested.

**pynng dials non-blocking.** `pynng.Req0(dial=url)` succeeds even when nothing
is listening; the failure only appears as a timeout on the first send. With a 30 s
operation timeout, a liveness check blocked for 30 seconds. Dial explicitly with
`block=True` so `ECONNREFUSED` surfaces immediately. Pinned by
`test_availability_check_fails_fast`.

**Python protobuf uses a different version line.** `libprotoc 35.1` pairs with
PyPI **`protobuf==7.35.x`** — `pip install protobuf==35.1` fails outright, since
no such version exists. Arch's `python-protobuf` renumbers to track the C++ line,
so `pacman` gives the right thing while pip does not. Verified working:
`libprotoc 35.1` + `protobuf 7.35.1` + `pynng 0.9.0`.

Also: KiCad's `api/CMakeLists.txt` only generates C++ (`.pb.cc`/`.pb.h`). Nothing
upstream produces Python bindings, so we generate our own —
`python -m autokicad.ipc.codegen`.

## Running the server from a build tree

`kicad-cli` resolves both the kiface and the stock data path relative to the
install prefix, so straight out of `build/` it fails twice:

```
Failed to load shared library '.../build/kicad/_pcbnew.kiface'
schema file '/usr/local/share/kicad/schemas/api.v1.schema.json' not found
```

Both are fixed by one environment variable:

```bash
cd build && KICAD_RUN_FROM_BUILD_DIR=1 \
  ./kicad/kicad-cli api-server --socket /tmp/my.sock path/to/board.kicad_pcb
```

`common/kiway.cpp:160` then goes up one directory and into the per-kiface
subdirectory (`build/pcbnew/_pcbnew.kiface`), and `common/paths.cpp:237` points
the stock data path at the build root — which already contains `schemas/`.

Incidentally `common/paths.cpp:290` reads `APPDIR` for the library data path,
which is the source-level confirmation of the AppImage leakage described in
[04-build.md](04-build.md).

## Verified against a live server

**2026-08-09** — first real exchange completed. Not loopback:

| Call | Result |
|---|---|
| `probe()` | `'10.99.0-unknown'` |
| `is_available()` | `True` |
| `version()` | `10.99.0-unknown` |
| `open_boards()` | 1 document, `ecc83-pp.kicad_pcb` |
| `nets(doc)` | 14 nets — `GND`, `Net-(P1-PM)`, … with codes |

So the full path works end to end: nng REQ0 → `ApiRequest` → `Any` pack → KiCad
dispatch by type URL → `ApiResponse` → `Any` unpack. The type-URL compatibility
predicted from reading `api_handler.cpp` holds against the real server.

Reproducible via `autokicad/tests/test_ipc_live.py`, which spawns a headless
server on a private socket, exercises it, and tears it down — 8 tests, ~6 s.
It skips cleanly without a master build, pynng, or generated bindings.

## What you still cannot do

No `RunDrc` or ERC message exists anywhere in `api/proto/**`. See
[03-gaps.md](03-gaps.md); it is the first thing to fix.
`test_ipc_live.py::test_drc_still_needs_the_cli` pins the current behaviour and
should be deleted when the handler lands.
