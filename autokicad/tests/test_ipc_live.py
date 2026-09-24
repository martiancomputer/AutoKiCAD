"""Live IPC tests against a real `kicad-cli api-server`.

Spawns a headless server on a private socket, talks to it, tears it down.
Skips cleanly unless all of these hold:

  * a KiCad **master** build exists at build/kicad/kicad-cli (`api-server` does
    not exist in released 10.x)
  * `pynng` and the protobuf runtime are importable
  * generated bindings are present

Marked `ipc_live` as well as `integration`, so it can be deselected on its own:

    pytest autokicad/tests -m "not ipc_live"
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from autokicad.backend import clean_env
from autokicad.ipc import IpcBackend, protos
from autokicad.ipc.client import ApiError

REPO = Path(__file__).resolve().parents[2]
KICAD_CLI = REPO / "build" / "kicad" / "kicad-cli"
DEMO = REPO / "demos" / "ecc83" / "ecc83-pp.kicad_pcb"

pytestmark = [pytest.mark.integration, pytest.mark.ipc_live]


def _why_skip() -> str | None:
    if not KICAD_CLI.is_file():
        return f"no master build at {KICAD_CLI.relative_to(REPO)}"
    if not DEMO.is_file():
        return "demo board missing"
    if not protos.available():
        return "protobuf runtime or generated bindings missing"
    if shutil.which("true") and not _has_pynng():
        return "pynng not installed"
    return None


def _has_pynng() -> bool:
    try:
        import pynng  # noqa: F401
        return True
    except ImportError:
        return False


skip_reason = _why_skip()
pytestmark.append(pytest.mark.skipif(skip_reason is not None, reason=skip_reason or ""))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A headless api-server on a private socket.

    `KICAD_RUN_FROM_BUILD_DIR` is required: without it KiCad resolves
    `_pcbnew.kiface` next to the executable (build/kicad/) rather than
    build/pcbnew/, and looks for schemas under the install prefix. See
    common/kiway.cpp:160 and common/paths.cpp:237.
    """
    sock = tmp_path_factory.mktemp("ipc") / "api.sock"
    log = sock.parent / "server.log"

    env = clean_env()          # strips APPDIR & friends
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"

    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [str(KICAD_CLI), "api-server", "--socket", str(sock), str(DEMO)],
            cwd=REPO / "build",
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Wait for the socket to appear rather than sleeping a fixed amount.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if sock.exists():
            break
        if proc.poll() is not None:
            pytest.fail(
                f"api-server exited early ({proc.returncode}):\n"
                f"{log.read_text(errors='replace')[:800]}"
            )
        time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail(f"api-server never created {sock}")

    prev = os.environ.get("KICAD_API_SOCKET")
    os.environ["KICAD_API_SOCKET"] = str(sock)
    try:
        yield sock
    finally:
        if prev is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = prev
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def backend(server):
    be = IpcBackend()
    yield be
    be.close()


# --------------------------------------------------------------------------


def test_server_is_reachable(backend):
    assert backend.is_available() is True


def test_version_round_trips(backend):
    """Proves the full path: REQ0 -> ApiRequest -> Any -> KiCad dispatch ->
    ApiResponse -> Any unpack."""
    v = backend.version()
    assert v
    assert v.startswith("10."), f"unexpected version {v!r}"


def test_preloaded_board_is_visible(backend):
    docs = backend.open_boards()
    assert len(docs) == 1
    assert docs[0].board_filename.endswith("ecc83-pp.kicad_pcb")


def test_nets_are_readable(backend):
    """Real board data, not just a handshake."""
    doc = backend.open_boards()[0]
    nets = backend.nets(doc)
    assert len(nets) > 1
    names = {n.name for n in nets}
    assert "GND" in names, f"expected GND among {sorted(names)[:10]}"


def test_net_codes_are_distinct(backend):
    doc = backend.open_boards()[0]
    nets = backend.nets(doc)
    codes = [n.code.value for n in nets]
    assert len(codes) == len(set(codes))


def test_repeated_calls_reuse_one_socket(backend):
    """REQ0 is a strict alternating state machine; consecutive calls on one
    socket must not desynchronise."""
    for _ in range(5):
        assert backend.version()


def test_probe_finds_the_server(server):
    from autokicad.ipc import probe

    assert probe() is not None


def test_drc_over_ipc_returns_violations(backend):
    """RunDrc -> structured Observation, no file on disk involved."""
    obs = backend.drc()
    assert obs.counts()["total"] >= 0
    assert obs.coordinate_units == "mm"
    # The severity filter defaults to error+warning when unset.
    assert set(obs.included_severities) <= {"error", "warning", "exclusion", "info"}


def test_drc_supported(backend):
    assert backend.supports_drc() is True


@pytest.fixture
def matched_cli(monkeypatch):
    """A CliBackend running the *same* binary and environment as the server.

    Comparing our master api-server against whatever `kicad-cli` is on PATH is
    not a controlled experiment: that is usually a released 10.x, and it also
    resolves footprint libraries against the install prefix rather than the
    build tree. The result is spurious `lib_footprint_issues` differences that
    say nothing about our handler. Point both sides at the same build.
    """
    from autokicad import CliBackend

    monkeypatch.setenv("KICAD_RUN_FROM_BUILD_DIR", "1")
    return CliBackend(exe=str(KICAD_CLI))


# Checks whose outcome depends on the footprint library table resolving, which
# api-server does not set up -- see test_library_parity_differs_under_api_server.
_LIBRARY_DEPENDENT = {"lib_footprint_issues", "lib_footprint_mismatch"}


def _comparable(obs):
    return sorted(
        (v.type, v.severity.value)
        for v in obs.of()
        if v.type not in _LIBRARY_DEPENDENT
    )


def test_ipc_drc_matches_cli_drc(backend, matched_cli):
    """The strongest check available: the new C++ path must agree exactly with
    the long-established `kicad-cli pcb drc` path, same binary, same board.

    Guards the whole chain -- provider walk, severity resolution, unit
    conversion, proto packing, and the Python mapping back to Observation.

    Library-parity checks are excluded because they reflect the server's library
    configuration rather than anything our handler does.
    """
    assert _comparable(backend.drc(DEMO)) == _comparable(matched_cli.drc(DEMO))


def test_library_parity_differs_under_api_server(backend, matched_cli):
    """Documents a real upstream gap, so it is not rediscovered as a bug in us.

    `kicad-cli api-server <board>` does not enable the project's footprint
    library table, so library-parity checks report
    "The footprint library 'X' is not enabled in the current configuration"
    for every footprint. `kicad-cli pcb drc` on the same board and binary loads
    the table and reports none.

    This is environmental, not a defect in handleRunDrc: severities, coordinates
    and every other check agree exactly (see test_ipc_drc_matches_cli_drc).

    If this test starts failing, upstream has likely fixed library-table setup in
    api-server -- delete it and drop the _LIBRARY_DEPENDENT filter.
    """
    ipc_lib = [v for v in backend.drc(DEMO).of() if v.type in _LIBRARY_DEPENDENT]
    cli_lib = [v for v in matched_cli.drc(DEMO).of() if v.type in _LIBRARY_DEPENDENT]

    assert not cli_lib, "CLI unexpectedly reported library issues"

    if ipc_lib:
        assert any("not enabled" in v.description for v in ipc_lib)


def test_ipc_drc_positions_are_millimetres(backend):
    """Regression: Vector2 is int64 *nanometres*, but RC_JSON coordinates are
    doubles already scaled to the report units. The first implementation stuffed
    67.429 mm into an nm field and truncated it to 67. Positions must survive as
    real millimetre values."""
    obs = backend.drc(DEMO)
    anchored = [v for v in obs.of() if v.anchor]

    if not anchored:
        pytest.skip("board produced no positioned violations")

    # A demo board sits at sane plotter coordinates; sub-millimetre values for
    # every violation would mean the nm/mm confusion is back.
    assert any(abs(v.anchor.x) > 1.0 or abs(v.anchor.y) > 1.0 for v in anchored)


def test_ipc_drc_matches_cli_positions(backend, matched_cli):
    """Coordinates must survive the nm/mm round trip identically on both paths."""
    pos = lambda o: sorted(
        (v.type, round(v.anchor.x, 3), round(v.anchor.y, 3))
        for v in o.of()
        if v.anchor and v.type not in _LIBRARY_DEPENDENT
    )
    assert pos(backend.drc(DEMO)) == pos(matched_cli.drc(DEMO))


# --------------------------------------------------------------------------
# Custom design rules -- the constraint pipeline the DDR acceptance test needs


MM = 1_000_000  # nanometres


def _rule_modules():
    return (
        protos.board_commands(),
        protos.base_types(),
        protos.load("board.board_pb2"),
    )


@pytest.fixture
def rules_client(backend):
    """Client plus document, with custom rules cleared afterwards.

    Rules persist to the board's .kicad_dru, so a test that leaves them behind
    would poison every later test in the module.
    """
    from autokicad.ipc import ApiClient

    bc, bt, _ = _rule_modules()
    client = ApiClient()
    doc = client.open_documents(bt.DocumentType.DOCTYPE_PCB)[0]
    try:
        yield client, doc
    finally:
        clear = bc.SetCustomDesignRules()
        clear.board.CopyFrom(doc)
        client.call(clear, bc.CustomRulesResponse)
        client.close()


def test_custom_rules_round_trip_verbatim(rules_client):
    """A real high-speed rule set must survive set/get unchanged.

    Modelled on demos/vme-wren/vme-wren.kicad_dru: per-layer differential
    impedance, a byte-lane length window with a fromTo() pin-pair condition, and
    an area-conditioned clearance. Those conditions are the parts most likely to
    be mangled, and they are exactly what a DDR interface needs.
    """
    from autokicad.ipc import ApiClient  # noqa: F401  (fixture already built one)

    bc, _, bd = _rule_modules()
    client, doc = rules_client
    CT, LM = bd.CustomRuleConstraintType, bd.CustomRuleLayerMode

    def mom(lo=None, opt=None, hi=None):
        v = protos.base_types().MinOptMax()
        if lo is not None:
            v.min = int(lo)
        if opt is not None:
            v.opt = int(opt)
        if hi is not None:
            v.max = int(hi)
        return v

    def rule(name, cond, constraints, layer_mode=None):
        r = bd.CustomRule()
        r.name, r.condition = name, cond
        if layer_mode is not None:
            r.layer_mode = layer_mode
        for ctype, val in constraints:
            c = r.constraints.add()
            c.type = ctype
            c.numeric.CopyFrom(val)
        return r

    sent = [
        rule(
            "zdiff_100R_outer",
            "A.inDiffPair('*')",
            [
                (CT.CRCT_TRACK_WIDTH, mom(0.115 * MM, 0.115 * MM, 0.115 * MM)),
                (CT.CRCT_DIFF_PAIR_GAP, mom(0.1 * MM, 0.1 * MM, 1 * MM)),
                (CT.CRCT_MAX_UNCOUPLED, mom(hi=5 * MM)),
            ],
            layer_mode=LM.CRLM_OUTER,
        ),
        rule(
            "length_DDR_Byte0",
            "A.NetClass == 'DDR4_BYTE0' && A.fromTo('IC14-*','IC13-*')",
            [(CT.CRCT_LENGTH, mom(29.5 * MM, 30 * MM, 30.5 * MM))],
        ),
        rule(
            "clearance_under_fpga",
            "A.intersectsArea('underFPGA') || A.intersectsArea('underDDR')",
            [
                (CT.CRCT_CLEARANCE, mom(lo=0.1 * MM)),
                (CT.CRCT_HOLE_SIZE, mom(lo=0.2 * MM)),
                (CT.CRCT_VIA_DIAMETER, mom(lo=0.4 * MM)),
            ],
        ),
    ]

    req = bc.SetCustomDesignRules()
    req.board.CopyFrom(doc)
    for r in sent:
        req.rules.add().CopyFrom(r)

    resp = client.call(req, bc.CustomRulesResponse)
    assert resp.status == 2, f"CRS_VALID expected, got {resp.status}: {resp.error_text}"

    get = bc.GetCustomDesignRules()
    get.board.CopyFrom(doc)
    back = client.call(get, bc.CustomRulesResponse)

    assert len(back.rules) == len(sent)
    for want, got in zip(sent, back.rules):
        assert got.name == want.name
        assert got.condition == want.condition, "rule condition was altered"
        key = lambda r: [
            (c.type, c.numeric.min, c.numeric.opt, c.numeric.max) for c in r.constraints
        ]
        assert key(got) == key(want)


def test_custom_rules_are_evaluated_by_drc(backend, rules_client):
    """Storing rules is not enough -- DRC must actually apply them.

    Regression: DRC_ENGINE caches parsed rules from whenever it was last
    initialised. In a long-lived api-server session that is board load, so rules
    written afterwards were silently ignored until handleRunDrc started calling
    InitEngine() before each run.
    """
    bc, _, bd = _rule_modules()
    client, doc = rules_client

    before = backend.drc()

    r = bd.CustomRule()
    r.name = "autokicad_test_clearance"
    r.condition = "A.Type == 'Track' && B.Type == 'Track'"
    con = r.constraints.add()
    con.type = bd.CustomRuleConstraintType.CRCT_CLEARANCE
    con.numeric.min = 5 * MM  # unsatisfiable on any real board

    req = bc.SetCustomDesignRules()
    req.board.CopyFrom(doc)
    req.rules.add().CopyFrom(r)
    assert client.call(req, bc.CustomRulesResponse).status == 2

    after = backend.drc()
    new = [v for v in after.of() if v.type == "clearance"]

    assert new, "custom rule was stored but not applied by DRC"
    assert any("autokicad_test_clearance" in v.description for v in new), (
        "violations must name the rule that produced them"
    )
    assert after.counts()["errors"] > before.counts()["errors"]


def test_clearing_custom_rules_reverts_drc(backend, rules_client):
    """Removing rules must restore the baseline, or state leaks between runs."""
    bc, _, bd = _rule_modules()
    client, doc = rules_client

    baseline = backend.drc().counts()

    r = bd.CustomRule()
    r.name = "autokicad_temp"
    r.condition = "A.Type == 'Track' && B.Type == 'Track'"
    con = r.constraints.add()
    con.type = bd.CustomRuleConstraintType.CRCT_CLEARANCE
    con.numeric.min = 5 * MM

    req = bc.SetCustomDesignRules()
    req.board.CopyFrom(doc)
    req.rules.add().CopyFrom(r)
    client.call(req, bc.CustomRulesResponse)
    assert backend.drc().counts() != baseline

    clear = bc.SetCustomDesignRules()
    clear.board.CopyFrom(doc)
    assert client.call(clear, bc.CustomRulesResponse).status == 1  # CRS_NONE

    assert backend.drc().counts() == baseline


def test_invalid_rule_is_rejected_not_stored(rules_client):
    """A malformed condition must come back as CRS_INVALID with parser detail,
    rather than being written and blowing up at DRC time."""
    bc, _, bd = _rule_modules()
    client, doc = rules_client

    r = bd.CustomRule()
    r.name = "broken"
    r.condition = "A.NetClass == "  # truncated expression
    con = r.constraints.add()
    con.type = bd.CustomRuleConstraintType.CRCT_CLEARANCE
    con.numeric.min = MM

    req = bc.SetCustomDesignRules()
    req.board.CopyFrom(doc)
    req.rules.add().CopyFrom(r)
    resp = client.call(req, bc.CustomRulesResponse)

    assert resp.status == 3, f"CRS_INVALID expected, got {resp.status}"
    assert resp.error_text


def test_unconnected_reported_separately(backend, tmp_path):
    """A stripped board must report unrouted nets in the unconnected bucket, and
    those must not leak into `errors` -- KiCad tags them severity=error."""
    import re

    from autokicad.models import Category

    src = DEMO
    text = src.read_text(encoding="utf-8")
    out, i, removed = [], 0, 0
    while i < len(text):
        if re.match(r"\((segment|arc|via)[\s(]", text[i:]):
            depth, p, in_str = 0, i, False
            while p < len(text):
                c = text[p]
                if in_str:
                    if c == "\\":
                        p += 2
                        continue
                    if c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        p += 1
                        break
                p += 1
            removed += 1
            i = p
            continue
        out.append(text[i])
        i += 1

    assert removed > 0
    stripped = tmp_path / "unrouted.kicad_pcb"
    stripped.write_text("".join(out), encoding="utf-8")

    # The live server holds a different board, so check this one via the CLI and
    # assert the model invariant that both backends share.
    from autokicad import CliBackend

    obs = CliBackend().drc(stripped)
    assert obs.unconnected_count > 0
    assert all(v.category is not Category.UNCONNECTED for v in obs.errors)


def test_net_chain_constraints_are_settable(rules_client):
    """Net-chain length and stub length must round-trip over the API.

    These are what T-topology address/command matching needs. They were
    rejected as "Unsupported custom rule constraint type" because
    DRC_RULE::FormatRuleFromProto() mapped no CRCT_NET_CHAIN_* case, even though
    DRC implements all of them and the .kicad_dru parser has always accepted the
    (unprefixed) keywords `net_chain_length` and `stub_length`.
    """
    bc, _, bd = _rule_modules()
    client, doc = rules_client
    CT = bd.CustomRuleConstraintType

    for name, ctype in (
        ("chain_total", CT.CRCT_NET_CHAIN_LENGTH),
        ("chain_stub", CT.CRCT_NET_CHAIN_STUB_LENGTH),
    ):
        r = bd.CustomRule()
        r.name = name
        r.condition = "A.NetClass == 'Default'"
        con = r.constraints.add()
        con.type = ctype
        con.numeric.max = 5 * MM

        req = bc.SetCustomDesignRules()
        req.board.CopyFrom(doc)
        req.rules.add().CopyFrom(r)
        resp = client.call(req, bc.CustomRulesResponse)

        assert resp.status == 2, f"{name}: {resp.error_text}"

        get = bc.GetCustomDesignRules()
        get.board.CopyFrom(doc)
        back = client.call(get, bc.CustomRulesResponse)

        assert len(back.rules) == 1
        assert back.rules[0].constraints[0].type == ctype
        assert back.rules[0].constraints[0].numeric.max == 5 * MM


def test_net_chain_return_path_still_unsupported(rules_client):
    """Documents a gap that a serialisation case cannot close.

    `return_path`'s rule form is (constraint return_path (layer "B.Cu")
    (net "GND")) -- a layer and a net, not a numeric value. CustomRuleConstraint's
    oneof has no variant carrying that pair, so exposing it needs a proto schema
    change. Delete this test when that lands.
    """
    bc, _, bd = _rule_modules()
    client, doc = rules_client

    r = bd.CustomRule()
    r.name = "chain_return"
    r.condition = "A.NetClass == 'Default'"
    con = r.constraints.add()
    con.type = bd.CustomRuleConstraintType.CRCT_NET_CHAIN_RETURN_PATH
    con.numeric.max = MM

    req = bc.SetCustomDesignRules()
    req.board.CopyFrom(doc)
    req.rules.add().CopyFrom(r)
    resp = client.call(req, bc.CustomRulesResponse)

    assert resp.status == 3, "expected CRS_INVALID until the proto carries layer+net"
    assert "Unsupported" in resp.error_text


# --------------------------------------------------------------------------
# Transmission-line calculations (no document required)


# FR-4, 1.6 mm substrate, 35 um copper, 1 GHz.
#
# TLP_TOP_HEIGHT matters and is easy to miss: it is the height of the enclosure
# lid, and KiCad's UI defaults it to 1e20 meaning "no cover". Every parameter a
# calculator declares is zero-initialised, so omitting it models a lid crushed
# onto the trace -- which yields *negative* impedance and an eps_eff that falls
# as the trace widens, rather than any error.
def _fr4(overrides=None):
    from autokicad.ipc import protos

    P = protos.base_commands()
    base = {
        P.TLP_EPSILON_R: 4.5,
        P.TLP_TAN_D: 0.02,
        P.TLP_SIGMA: 5.96e7,
        P.TLP_SUBSTRATE_HEIGHT: 1.6e-3,
        P.TLP_TOP_HEIGHT: 1e20,
        P.TLP_CONDUCTOR_THICKNESS: 35e-6,
        P.TLP_FREQUENCY: 1e9,
        P.TLP_PHYS_LENGTH: 10e-3,
        P.TLP_MU_R: 1.0,
        P.TLP_MU_R_CONDUCTOR: 1.0,
    }
    base.update(overrides or {})
    return base


def _transline(client, tl_type, mode, params, target=None):
    from autokicad.ipc import protos

    bc = protos.base_commands()
    req = bc.RunTransmissionLineCalculation()
    req.type = tl_type
    req.mode = mode

    for key, value in params.items():
        entry = req.parameters.add()
        entry.parameter = key
        entry.value = value

    if target is not None:
        req.synthesize_target = target

    resp = client.call(req, bc.RunTransmissionLineCalculationResponse)
    return {v.parameter: v.value for v in resp.parameters}, resp.converged


@pytest.fixture
def api(server):
    from autokicad.ipc import ApiClient

    client = ApiClient()
    yield client
    client.close()


def test_microstrip_impedance_falls_with_width(api):
    """Basic physics: a wider trace over the same substrate has lower impedance,
    and more of its field in the dielectric so a higher effective permittivity."""
    from autokicad.ipc import protos

    P = protos.base_commands()
    results = []

    for width in (0.5e-3, 1.0e-3, 3.0e-3, 6.0e-3):
        out, _ = _transline(api, P.TLT_MICROSTRIP, P.TLM_ANALYSE,
                            _fr4({P.TLP_PHYS_WIDTH: width}))
        results.append((out[P.TLP_Z0], out[P.TLP_EPSILON_EFF]))

    impedances = [z for z, _ in results]
    eps_eff = [e for _, e in results]

    assert all(z > 0 for z in impedances), f"non-physical impedance: {impedances}"
    assert impedances == sorted(impedances, reverse=True), impedances
    assert eps_eff == sorted(eps_eff), eps_eff

    # Bounded by air and bulk FR-4.
    assert all(1.0 < e < 4.5 for e in eps_eff), eps_eff


def test_microstrip_synthesis_hits_the_target(api):
    """Solve for the width giving 50 ohm, then analyse that width back.

    ~2.9-3.0 mm on 1.6 mm FR-4 is the standard result, so this catches a solver
    that converges on something self-consistent but physically wrong.
    """
    from autokicad.ipc import protos

    P = protos.base_commands()

    out, converged = _transline(
        api, P.TLT_MICROSTRIP, P.TLM_SYNTHESISE,
        _fr4({P.TLP_Z0: 50.0, P.TLP_PHYS_WIDTH: 1.0e-3}),
        target=P.TLP_PHYS_WIDTH,
    )

    assert converged
    width = out[P.TLP_PHYS_WIDTH]
    assert 2.5e-3 < width < 3.5e-3, f"50 ohm width {width * 1e3:.3f} mm is implausible"

    check, _ = _transline(api, P.TLT_MICROSTRIP, P.TLM_ANALYSE,
                          _fr4({P.TLP_PHYS_WIDTH: width}))
    assert abs(check[P.TLP_Z0] - 50.0) < 0.5


def test_transline_rejects_inapplicable_parameter(api):
    """A parameter a geometry does not model must be a clear error.

    TRANSLINE_CALCULATION_BASE reads its map with .at(), so an unsupported
    parameter throws; unguarded that escapes the handler and the caller sees only
    a socket timeout.
    """
    from autokicad.ipc import protos
    from autokicad.ipc.client import ApiError

    P = protos.base_commands()

    with pytest.raises(ApiError, match="does not apply"):
        _transline(api, P.TLT_MICROSTRIP, P.TLM_ANALYSE,
                   _fr4({P.TLP_PHYS_DIAMETER_IN: 1e-3}))


def test_transline_rejects_unknown_type(api):
    from autokicad.ipc import protos
    from autokicad.ipc.client import ApiError

    P = protos.base_commands()

    with pytest.raises(ApiError, match="unknown or unset"):
        _transline(api, P.TLT_UNKNOWN, P.TLM_ANALYSE, _fr4())


def test_coax_impedance_is_plausible(api):
    """A second geometry, to prove the dispatch is not microstrip-only.

    RG-58-ish: 0.9 mm inner, 2.95 mm dielectric, PE at eps_r 2.3 -> ~50 ohm.
    """
    from autokicad.ipc import protos

    P = protos.base_commands()

    out, _ = _transline(api, P.TLT_COAX, P.TLM_ANALYSE, {
        P.TLP_EPSILON_R: 2.3,
        P.TLP_TAN_D: 0.0002,
        P.TLP_SIGMA: 5.96e7,
        P.TLP_PHYS_DIAMETER_IN: 0.9e-3,
        P.TLP_PHYS_DIAMETER_OUT: 2.95e-3,
        P.TLP_FREQUENCY: 1e9,
        P.TLP_PHYS_LENGTH: 100e-3,
        P.TLP_MU_R: 1.0,
        P.TLP_MU_R_CONDUCTOR: 1.0,
    })

    assert 40.0 < out[P.TLP_Z0] < 60.0, f"coax Z0 {out[P.TLP_Z0]:.2f} ohm"


# ---- autoplacement -------------------------------------------------------
#
# Placement mutates the open board in memory, so it gets its own server on its
# own copy. Sharing the module `server` would move footprints under the DRC
# tests, which compare IPC results against the CLI reading the *saved* file.


def _server_on(board: Path, tmp: Path):
    """Start an api-server on `board`, yielding (proc, socket). Caller tears down."""
    sock = tmp / "api.sock"
    log = tmp / "server.log"

    env = clean_env()
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"

    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [str(KICAD_CLI), "api-server", "--socket", str(sock), str(board)],
            cwd=REPO / "build",
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if sock.exists():
            return proc, sock
        if proc.poll() is not None:
            pytest.fail(
                f"api-server exited early ({proc.returncode}):\n"
                f"{log.read_text(errors='replace')[:800]}"
            )
        time.sleep(0.25)

    proc.kill()
    pytest.fail(f"api-server never created {sock}")


def _stop(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


def _client_on(board: Path, tmp: Path):
    """(client, document, teardown) for a private server holding a copy of `board`."""
    from autokicad.ipc import ApiClient

    proc, sock = _server_on(board, tmp)
    prev = os.environ.get("KICAD_API_SOCKET")
    os.environ["KICAD_API_SOCKET"] = str(sock)

    # Anything failing between here and the return would otherwise leak the server:
    # the caller has no handle to tear down yet. Six of these accumulated the first
    # time this raised.
    try:
        client = ApiClient()
        docs = client.open_documents(protos.base_types().DocumentType.DOCTYPE_PCB)
        assert docs, "server opened no board"
    except BaseException:
        if prev is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = prev
        _stop(proc)
        raise

    def teardown():
        client.close()
        if prev is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = prev
        _stop(proc)

    return client, docs[0], teardown


@pytest.fixture
def placement(tmp_path):
    """A private server on a copy of the demo board, safe to mutate."""
    board = tmp_path / "board.kicad_pcb"
    shutil.copy(DEMO, board)

    client, doc, teardown = _client_on(board, tmp_path)
    try:
        yield client, doc
    finally:
        teardown()


def _footprints(client, doc) -> list:
    """Every FootprintInstance on the board, unpacked."""
    ed = protos.editor_commands()
    req = ed.GetItems()
    req.header.document.CopyFrom(doc)
    req.types.append(protos.enums().KiCadObjectType.KOT_PCB_FOOTPRINT)
    resp = client.call(req, ed.GetItemsResponse)

    out = []
    for item in resp.items:
        fp = protos.board_types().FootprintInstance()
        item.Unpack(fp)
        out.append(fp)
    return out


def _footprint_ids(client, doc) -> list[str]:
    return [fp.id.value for fp in _footprints(client, doc)]


def test_autoplace_moves_footprints(placement):
    """The headline behaviour: AR_AUTOPLACER runs to completion with no frame, no
    view and no progress reporter, and actually relocates parts."""
    client, doc = placement

    resp = client.autoplace_footprints(doc)

    P = protos.board_commands()
    assert resp.result == P.APR_COMPLETED, f"result was {resp.result}"
    assert resp.moved, "autoplacement claimed success but moved nothing"


def test_autoplace_actually_changes_positions(placement):
    """`moved` is derived from before/after coordinates, so confirm independently
    that the board really changed -- a placer that reported success while leaving
    everything put would otherwise look identical."""
    client, doc = placement

    before = {f.id.value: (f.position.x_nm, f.position.y_nm) for f in _footprints(client, doc)}
    resp = client.autoplace_footprints(doc)
    after = {f.id.value: (f.position.x_nm, f.position.y_nm) for f in _footprints(client, doc)}

    changed = {k for k in before if before[k] != after.get(k)}
    assert changed, "no footprint position changed on the board"
    assert changed == {k.value for k in resp.moved}, "reported `moved` disagrees with the board"


def test_autoplace_named_subset_moves_only_those(placement):
    """A caller naming footprints gets exactly those moved -- placement must not
    quietly rearrange the rest of the board."""
    client, doc = placement

    ids = _footprint_ids(client, doc)
    assert len(ids) > 3, f"expected a populated demo board, got {len(ids)} footprints"
    chosen = set(ids[:3])

    resp = client.autoplace_footprints(doc, footprints=sorted(chosen))

    assert {k.value for k in resp.moved} <= chosen, "moved a footprint that was not requested"


def test_autoplace_rejects_unknown_footprint(placement):
    """A bad KIID is a request error, not a silent no-op."""
    client, doc = placement

    with pytest.raises(ApiError) as exc:
        client.autoplace_footprints(
            doc, footprints=["00000000-0000-0000-0000-0000deadbeef"]
        )

    assert "not found" in str(exc.value).lower(), str(exc.value)


def test_autoplace_skips_locked_footprints(placement, tmp_path):
    """AR_AUTOPLACER has no lock awareness at all -- AUTOPLACE_TOOL filters locked
    footprints before calling it. The handler has to do the same, or a locked part
    gets moved silently over the API."""
    client, doc = placement

    fp = _footprints(client, doc)[0]
    target = fp.id.value

    ed = protos.editor_commands()
    fp.locked = protos.base_types().LockedState.LS_LOCKED
    update = ed.UpdateItems()
    update.header.document.CopyFrom(doc)
    update.items.add().Pack(fp)
    client.call(update, ed.UpdateItemsResponse)

    # Naming only a locked footprint leaves nothing placeable, which is a request
    # error rather than a success that moved nothing.
    with pytest.raises(ApiError) as exc:
        client.autoplace_footprints(doc, footprints=[target])

    assert "locked" in str(exc.value).lower(), str(exc.value)

    # ...and override_locks is the way through, mirroring the editor's
    # GetOverrideLocks().
    resp = client.autoplace_footprints(doc, footprints=[target], override_locks=True)
    assert resp.result == protos.board_commands().APR_COMPLETED


def test_autoplace_without_board_outline_is_a_clear_error(tmp_path):
    """AR_AUTOPLACER answers a bare AR_FAILURE when the outline is degenerate --
    genPlacementRoutingMatrix() returns 0 and nothing says why. The handler checks
    first, the way AUTOPLACE_TOOL does, so the caller learns the actual cause."""
    src = DEMO.read_text(encoding="utf-8")
    board = tmp_path / "no_outline.kicad_pcb"
    # Move every Edge.Cuts item to a silkscreen layer: the outline disappears while
    # the file stays structurally valid.
    board.write_text(src.replace('"Edge.Cuts"', '"F.SilkS"'), encoding="utf-8")

    client, doc, teardown = _client_on(board, tmp_path)
    try:
        with pytest.raises(ApiError) as exc:
            client.autoplace_footprints(doc)

        assert "edge" in str(exc.value).lower(), str(exc.value)
    finally:
        teardown()


# ---- libraries -----------------------------------------------------------
#
# These need a server that can actually see libraries, which takes two things the
# default fixture does not provide:
#
#   * a global library table. A fresh 10.99 settings profile has none -- KiCad
#     seeds them from /usr/share/kicad/template on first GUI run, and nothing in
#     the CLI path does it.
#   * KICAD10_FOOTPRINT_DIR / KICAD10_SYMBOL_DIR. The stock path is derived from
#     the build's install prefix, so our /usr/local build resolves every global
#     library to /usr/local/share/kicad/... which does not exist.
#
# Both are environmental, so the fixture supplies them rather than the tests
# asserting around them.

STOCK_FOOTPRINTS = Path("/usr/share/kicad/footprints")
STOCK_SYMBOLS = Path("/usr/share/kicad/symbols")
LIB_TEMPLATES = Path("/usr/share/kicad/template")

needs_stock_libs = pytest.mark.skipif(
    not (STOCK_FOOTPRINTS.is_dir() and (LIB_TEMPLATES / "fp-lib-table").is_file()),
    reason="stock KiCad libraries or lib-table templates not installed",
)


@pytest.fixture(scope="module")
def library_server(tmp_path_factory):
    """A server that can see the stock libraries, on the demo's own project."""
    tmp = tmp_path_factory.mktemp("libs")
    cfg = tmp / "cfg" / "10.99"
    cfg.mkdir(parents=True)

    for name in ("fp-lib-table", "sym-lib-table"):
        src = LIB_TEMPLATES / name
        if src.is_file():
            shutil.copy(src, cfg / name)

    sock = tmp / "api.sock"
    log = tmp / "server.log"

    env = clean_env()
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"
    env["KICAD_CONFIG_HOME"] = str(tmp / "cfg")
    env["KICAD10_FOOTPRINT_DIR"] = str(STOCK_FOOTPRINTS)
    env["KICAD10_SYMBOL_DIR"] = str(STOCK_SYMBOLS)

    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [str(KICAD_CLI), "api-server", "--socket", str(sock), str(DEMO)],
            cwd=REPO / "build",
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if sock.exists():
            break
        if proc.poll() is not None:
            pytest.fail(
                f"api-server exited early ({proc.returncode}):\n"
                f"{log.read_text(errors='replace')[:800]}"
            )
        time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail(f"api-server never created {sock}")

    prev = os.environ.get("KICAD_API_SOCKET")
    os.environ["KICAD_API_SOCKET"] = str(sock)
    try:
        yield sock
    finally:
        if prev is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = prev
        _stop(proc)


@pytest.fixture
def libs(library_server):
    from autokicad.ipc import ApiClient

    # A cold fp-info-cache means the first all-library search reads every configured
    # library, so this needs more headroom than the default timeout.
    client = ApiClient(timeout_ms=600_000)
    doc = client.open_documents(protos.base_types().DocumentType.DOCTYPE_PCB)[0]
    yield client, doc
    client.close()


@needs_stock_libs
def test_list_libraries_finds_the_stock_footprint_libraries(libs):
    """The first thing an agent needs: which libraries exist at all."""
    client, _ = libs
    B = protos.base_commands()

    libraries = client.list_libraries(B.LBT_FOOTPRINT)

    assert len(libraries) > 50, f"only {len(libraries)} footprint libraries"
    assert any(lib.nickname == "Resistor_SMD" for lib in libraries)


@needs_stock_libs
def test_listed_libraries_resolve_to_real_paths(libs):
    """`uri` keeps ${KICAD10_FOOTPRINT_DIR} unexpanded, so it looks correct even when
    the variable points nowhere. `resolved_uri` is what actually gets read, and on a
    build whose install prefix differs from the installed libraries it is the only
    field that shows the mismatch."""
    client, _ = libs
    B = protos.base_commands()

    globals_ = [
        lib for lib in client.list_libraries(B.LBT_FOOTPRINT) if lib.scope == B.LBS_GLOBAL
    ]
    assert globals_, "no global footprint libraries listed"

    missing = [lib.nickname for lib in globals_ if not Path(lib.resolved_uri).exists()]
    assert not missing, f"{len(missing)} libraries resolve to nonexistent paths: {missing[:3]}"


@needs_stock_libs
def test_list_libraries_includes_the_project_library(libs):
    """Project-scope rows must be reported too -- a ${KIPRJMOD} library is exactly
    the kind a design depends on, and it is invisible in the global table."""
    client, _ = libs
    B = protos.base_commands()

    project = [
        lib for lib in client.list_libraries(B.LBT_FOOTPRINT) if lib.scope == B.LBS_PROJECT
    ]

    assert [lib.nickname for lib in project] == ["Footprints"]
    assert project[0].enabled and project[0].ok


@needs_stock_libs
def test_list_libraries_scope_filter(libs):
    """Asking for one scope must not quietly return both."""
    client, _ = libs
    B = protos.base_commands()

    only_project = client.list_libraries(B.LBT_FOOTPRINT, scope=B.LBS_PROJECT)

    assert only_project
    assert all(lib.scope == B.LBS_PROJECT for lib in only_project)


@needs_stock_libs
def test_list_libraries_rejects_unspecified_type(libs):
    """The type is not optional: there is no sensible default table to read."""
    client, _ = libs
    B = protos.base_commands()

    with pytest.raises(ApiError) as exc:
        client.list_libraries(B.LBT_UNKNOWN)

    assert "library type" in str(exc.value).lower(), str(exc.value)


@needs_stock_libs
def test_list_symbol_libraries(libs):
    """Symbol libraries list from the same call; only entry *search* is
    footprint-only so far."""
    client, _ = libs
    B = protos.base_commands()

    libraries = client.list_libraries(B.LBT_SYMBOL)

    assert len(libraries) > 50, f"only {len(libraries)} symbol libraries"
    assert any(lib.nickname == "Device" for lib in libraries)


@needs_stock_libs
def test_search_footprints_scoped_to_a_library(libs):
    """The core question: resolve a part to a LIB_ID."""
    client, doc = libs

    resp = client.search_footprints(doc, "R_0603", libraries=["Resistor_SMD"])

    names = {r.id.entry_name for r in resp.results}
    assert "R_0603_1608Metric" in names, names
    assert all(r.id.library_nickname == "Resistor_SMD" for r in resp.results)


@needs_stock_libs
def test_search_footprints_returns_metadata(libs):
    """Name alone is not enough to choose a part; description and pad count are what
    let a caller check a footprint against a symbol."""
    client, doc = libs

    resp = client.search_footprints(doc, "R_0603_1608Metric", libraries=["Resistor_SMD"])

    hit = next(r for r in resp.results if r.id.entry_name == "R_0603_1608Metric")
    assert hit.pad_count == 2, hit.pad_count
    assert "0603" in hit.description, hit.description


@needs_stock_libs
def test_search_footprints_across_all_libraries(libs):
    """Unscoped search over the whole stock set -- the "what is available?" case."""
    client, doc = libs

    resp = client.search_footprints(doc, "0603 resistor", match_all_terms=True, max_results=5)

    assert resp.total_indexed > 5_000, resp.total_indexed
    assert len(resp.results) == 5
    assert resp.truncated, "5 of thousands of matches should report truncated"


@needs_stock_libs
def test_match_all_terms_changes_the_result(libs):
    """Without it the query is one substring, so a multi-word query matches nothing.
    This is the difference between a usable search and an empty one."""
    client, doc = libs

    whole = client.search_footprints(doc, "0603 resistor", libraries=["Resistor_SMD"])
    terms = client.search_footprints(
        doc, "0603 resistor", libraries=["Resistor_SMD"], match_all_terms=True
    )

    assert not whole.results, "expected no match for the literal string '0603 resistor'"
    assert terms.results, "expected matches when each term is required separately"


@needs_stock_libs
def test_search_footprints_rejects_unknown_library(libs):
    """A typo'd nickname is a request error, not an empty result set."""
    client, doc = libs

    with pytest.raises(ApiError) as exc:
        client.search_footprints(doc, "R_0603", libraries=["No_Such_Library"])

    assert "no such footprint library" in str(exc.value).lower(), str(exc.value)
