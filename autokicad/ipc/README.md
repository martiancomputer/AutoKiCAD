# autokicad.ipc — KiCad IPC API client

Protobuf over nng, talking to a live KiCad. This is the transport that replaces
the removed SWIG bindings (`import pcbnew` does not exist on KiCad master —
commit `65a442b1d2` removed SWIG, wxPython and embedded Python entirely).

## Read this first: what this cannot do yet

**There is no DRC or ERC command anywhere in `api/proto/**`.** The board schema
exposes stackup, design rules, nets, connectivity queries, item CRUD and 14
export jobs, but nothing that *runs* the checker. (`InjectDrcError` reports a
violation into the editor; it does not run DRC.)

So a pure-IPC `observe()` is impossible today. `IpcBackend` runs DRC through
`CliBackend` and uses IPC for what IPC is uniquely good at: **live editor
state**. That distinction is real — the CLI only ever reads the last *saved*
file from disk, while IPC sees the board as it currently is, unsaved edits
included.

Closing this gap is the first C++ milestone: add a `RunDrc` message plus a
handler in `pcbnew/api/api_handler_board.cpp`. When it lands, `IpcBackend.drc()`
becomes one `self.client.call(...)` and the returned `Observation` is unchanged —
which is the entire reason `models.py` is transport-independent.

## Setup

```bash
python -m autokicad.ipc.codegen          # 1. generate bindings from api/proto/**
sudo pacman -S python-protobuf           # 2. runtime
python -m venv .venv && .venv/bin/pip install pynng   # 3. transport
```

Then a server: KiCad with the API enabled, or `kicad-cli api-server`.
**`api-server` is master-only** — released 10.x has just
`fp, jobset, pcb, sch, sym, version`.

### Version pairing gotcha

protobuf numbers its Python runtime differently from its C++ toolchain. `libprotoc
35.1` pairs with PyPI **`protobuf==7.35.x`**, not `35.1` (Arch renumbers its
`python-protobuf` to track the C++ line, so `pacman` gives you the right thing
but `pip install protobuf==35.1` fails outright). Verified working here:
`libprotoc 35.1` + `protobuf 7.35.1` + `pynng 0.9.0`.

## Usage

```python
from autokicad import CliBackend
from autokicad.ipc import IpcBackend, probe, best_backend

if probe():                                  # cheap liveness check, never raises
    with IpcBackend(cli=CliBackend()) as be:
        print(be.version())
        print(be.open_boards())              # unsaved editor state

be = best_backend()                          # IPC if live, else CLI; both are Backend
```

## Wire protocol

Confirmed against the C++ source rather than assumed:

| Aspect | Value | Source |
|---|---|---|
| Socket type | nng **REP0** server, so clients use **REQ0** | `libs/kinng/src/kinng.cpp` (`nng_rep0_open`) |
| Default URL | `ipc:///tmp/kicad/api.sock` | `common/api/api_server.cpp` |
| Collision fallback | `api-<pid>.sock` when `api.sock` is taken | same |
| Envelope | `ApiRequest{header{kicad_token, client_name}, Any}` → `ApiResponse{header, status, Any}` | `api/proto/common/envelope.proto` |
| Dispatch | `Any::ParseAnyTypeUrl(type_url)` matched against `RequestType().GetTypeName()` | `common/api/api_handler.cpp:43`, `include/api/api_handler.h:96` |
| Credentials | `KICAD_API_SOCKET`, `KICAD_API_TOKEN` env vars | `common/api/api_plugin_manager.cpp:391` |

Because dispatch is by fully-qualified proto name, Python's stock `Any.Pack()`
(which emits `type.googleapis.com/kiapi.common.commands.Ping`) interoperates
with no custom type-URL handling. Pinned by
`test_any_type_url_matches_kicad_dispatch`.

## Error handling

`ApiStatusCode` is mapped to exceptions, and **only transient codes are
retried** — `AS_BUSY` and `AS_NOT_READY`, which a live editor returns routinely
while mid-operation or still starting. `AS_BAD_REQUEST` and friends fail
immediately rather than retrying a request that can never succeed.

- `ApiBusy` — AS_BUSY / AS_NOT_READY (retried)
- `TokenMismatch` — AS_TOKEN_MISMATCH, i.e. wrong/missing `KICAD_API_TOKEN`
- `ApiUnimplemented` — AS_UNIMPLEMENTED, in the schema but no handler
- `ApiError` — everything else

## Fail-fast liveness

`PynngTransport` dials with `block=True`. pynng's `Req0(dial=...)` kwarg dials
*non-blocking*: construction succeeds against a dead socket and the failure only
appears as a timeout on the first send, which made `is_available()` block for the
full 30 s operation timeout. An agent doing a liveness check cannot afford that.
Pinned by `test_availability_check_fails_fast`.

## Generated code

`_proto/` is generated, not committed (see `../.gitignore`) — it must match the
`api/proto/**` in your checked-out tree. `codegen.py --check` exits non-zero when
the protos are newer than the bindings; wire that into CI.

`protos.py` puts `_proto/` on `sys.path` because protoc emits imports relative
to `--proto_path` (`board/board_commands_pb2.py` contains
`from common.types import base_types_pb2`), which only resolve with the
generation root importable. Generated code is left unmodified.

## Status

Verified: codegen for all 16 protos, envelope round-trip, type-URL compatibility,
token propagation from env, retry policy, status→exception mapping, socket
discovery including `api-<pid>.sock`, fail-fast dial, and CLI delegation.

Unverified, and honestly so: **no exchange with a real KiCad server has
happened**, because `api-server` needs the master build that is still compiling.
Everything above is tested against `LoopbackTransport`. The first real
connection is the next thing to do once the build lands.
