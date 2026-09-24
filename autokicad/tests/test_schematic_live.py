"""Live tests for the schematic API, against a real eeschema GUI.

eeschema has no headless mode, so these need an X display. They run on their own
Xvfb rather than the developer's session: the fixture owns that display and tears
it down, which `live-gui.sh` deliberately never does.

Skips cleanly unless all of these hold:

  * a master build at build/eeschema/eeschema
  * Xvfb on PATH
  * the stock symbol libraries and lib-table templates installed
  * pynng and the generated protobuf bindings importable

Marked `schematic_live` as well as `integration`, so it can be deselected:

    pytest autokicad/tests -m "not schematic_live"
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from autokicad.backend import clean_env

REPO = Path(__file__).resolve().parents[2]
EESCHEMA = REPO / "build" / "eeschema" / "eeschema"
STOCK = Path("/usr/share/kicad")

pytestmark = [pytest.mark.integration, pytest.mark.schematic_live]

# A KiCad-written empty sheet. Hand-writing one is a trap: the file format version
# must match the build (this one writes 20260722), and an older version silently
# fails to load, leaving eeschema on an "untitled" document whose sheet path does
# not resolve.
BLANK_SCH = """(kicad_sch
\t(version 20260722)
\t(generator "eeschema")
\t(generator_version "10.99")
\t(uuid "aaaaaaaa-0000-4000-8000-00000000test")
\t(paper "A3")
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""


def _why_skip() -> str | None:
    if not EESCHEMA.is_file():
        return f"no eeschema build at {EESCHEMA.relative_to(REPO)}"
    if not shutil.which("Xvfb"):
        return "Xvfb not installed"
    if not (STOCK / "symbols").is_dir():
        return "stock KiCad symbol libraries not installed"
    if not (STOCK / "template" / "sym-lib-table").is_file():
        return "lib-table templates not installed"

    try:
        import pynng  # noqa: F401
    except ImportError:
        return "pynng not installed"

    from autokicad.ipc import protos

    if not protos.available():
        return "protobuf runtime or generated bindings missing"

    return None


skip_reason = _why_skip()
pytestmark.append(pytest.mark.skipif(skip_reason is not None, reason=skip_reason or ""))


def _free_display() -> str:
    """A display number with no X socket, so parallel runs do not collide."""
    for n in range(90, 130):
        if not Path(f"/tmp/.X11-unix/X{n}").exists():
            return f":{n}"
    raise RuntimeError("no free X display in :90-:129")


def _free_socket_path(tmp: Path) -> Path:
    return tmp / "api.sock"


def _seed_config(cfg: Path) -> None:
    versioned = cfg / "10.99"
    versioned.mkdir(parents=True, exist_ok=True)

    (versioned / "kicad_common.json").write_text(
        '{\n'
        '  "api": { "enable_server": true, "interpreter_path": "" },\n'
        '  "environment": { "show_warning_dialog": false },\n'
        '  "do_not_show_again": {\n'
        '    "update_check_prompt": true,\n'
        '    "data_collection_prompt": true\n'
        '  }\n'
        '}\n'
    )

    for name in ("sym-lib-table", "fp-lib-table"):
        src = STOCK / "template" / name
        if src.is_file():
            shutil.copy(src, versioned / name)

    # Load-bearing. InvalidGlobalTables() checks symbol, footprint *and* design
    # block; KiCad ships no design-block template, so without this the first-run
    # wizard runs. It is modal inside OnPgmInit, ahead of SetReadyToReply(), so the
    # API server answers AS_NOT_READY forever and every test times out.
    (versioned / "design-block-lib-table").write_text(
        "(design_block_lib_table\n\t(version 7)\n)\n"
    )


@pytest.fixture(scope="module")
def eeschema_session(tmp_path_factory):
    """An eeschema on a private Xvfb, with a blank schematic open."""
    tmp = tmp_path_factory.mktemp("sch")
    display = _free_display()
    cfg = tmp / "config"
    _seed_config(cfg)

    sheet = tmp / "test.kicad_sch"
    sheet.write_text(BLANK_SCH)

    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1600x1200x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if Path(f"/tmp/.X11-unix/X{display[1:]}").exists():
            break
        if xvfb.poll() is not None:
            pytest.fail(f"Xvfb exited early ({xvfb.returncode})")
        time.sleep(0.25)
    else:
        xvfb.kill()
        pytest.fail(f"Xvfb never came up on {display}")

    env = clean_env()
    env["DISPLAY"] = display
    env["KICAD_RUN_FROM_BUILD_DIR"] = "1"
    env["KICAD_CONFIG_HOME"] = str(cfg)
    env["KICAD10_SYMBOL_DIR"] = str(STOCK / "symbols")
    env["KICAD10_FOOTPRINT_DIR"] = str(STOCK / "footprints")

    log = tmp / "eeschema.log"

    with log.open("wb") as fh:
        proc = subprocess.Popen(
            [str(EESCHEMA), str(sheet)],
            cwd=REPO / "build",
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    # The GUI's API server always listens on /tmp/kicad/api.sock; KICAD_API_SOCKET
    # is what KiCad passes to plugins and does not move the server.
    sock = Path("/tmp/kicad/api.sock")
    prev = os.environ.get("KICAD_API_SOCKET")
    os.environ["KICAD_API_SOCKET"] = str(sock)

    from autokicad.ipc import ApiClient
    from autokicad.ipc.client import ApiBusy

    session = None
    deadline = time.monotonic() + 120

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if sock.exists():
            try:
                # Answers AS_NOT_READY until OnPgmInit finishes, so poll rather than
                # assuming the socket appearing means it is usable.
                ApiClient(timeout_ms=5_000).version()
                from autokicad.schematic import SchematicSession

                session = SchematicSession.connect(heartbeat=False)
                break
            except Exception:
                pass
        time.sleep(2)

    if session is None:
        proc.kill()
        xvfb.kill()
        pytest.fail(
            "eeschema never became ready:\n" + log.read_text(errors="replace")[:1500]
        )

    try:
        yield session
    finally:
        try:
            session.close()
        except Exception:
            pass

        if prev is None:
            os.environ.pop("KICAD_API_SOCKET", None)
        else:
            os.environ["KICAD_API_SOCKET"] = prev

        for p in (proc, xvfb):
            try:
                os.killpg(os.getpgid(p.pid), 15)
            except Exception:
                p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()


# --------------------------------------------------------------------------


def test_search_finds_a_stock_symbol(eeschema_session):
    """The step before placing anything: resolve a part from a text query."""
    s = eeschema_session
    r = s.search_symbols("R", libraries=["Device"], max_results=10)

    assert r.total_indexed > 100, r.total_indexed
    assert any(x.id.entry_name == "R" for x in r.results)


def test_search_ranks_exact_name_first(eeschema_session):
    """Regression: searching power symbols for GND returned Earth first, because its
    keywords are "global ground gnd" while the symbol actually named GND sorted
    fourth. A caller taking results[0] then wired the board to a net called Earth."""
    s = eeschema_session
    r = s.search_symbols("GND", power_only=True, max_results=5)

    assert r.results, "no power symbols matched GND"
    assert r.results[0].id.entry_name == "GND", [x.id.entry_name for x in r.results]


def test_search_match_all_terms_narrows(eeschema_session):
    """Without it a multi-word query is one substring and matches nothing useful."""
    s = eeschema_session

    whole = s.search_symbols("resistor us", libraries=["Device"])
    terms = s.search_symbols("resistor us", libraries=["Device"], match_all_terms=True)

    assert len(terms.results) > len(whole.results)


def test_search_rejects_unknown_library(eeschema_session):
    """A typo'd nickname is a request error, not an empty result set."""
    from autokicad.ipc.client import ApiError

    s = eeschema_session

    with pytest.raises(ApiError) as exc:
        s.search_symbols("R", libraries=["No_Such_Library"])

    assert "no such symbol library" in str(exc.value).lower(), str(exc.value)


def test_place_symbol_has_body_and_pins(eeschema_session):
    """The headline behaviour, and the thing that was broken for a long time:
    CreateItems with a bare LIB_ID produced a symbol with no body and no pins."""
    s = eeschema_session
    sym = s.place_symbol("Device:R", 40, 40, reference="RT1", value="1k")

    assert sym.definition.id.entry_name == "R"
    assert len(s.pin_positions(sym)) == 2, s.pin_positions(sym)


def test_placed_symbol_is_visible_to_get_items(eeschema_session):
    """Regression: without a sheet path the SCH_SYMBOL_INSTANCE lands under an empty
    KIID_PATH, and the symbol is then unreachable here while CreateItems still
    reports success."""
    from autokicad.ipc import protos

    s = eeschema_session
    s.place_symbol("Device:C", 60, 40, reference="CT1", value="10n")

    ed, E, st = protos.editor_commands(), protos.enums(), protos.schematic_types()
    q = ed.GetItems()
    q.header.document.CopyFrom(s.document)
    q.types.append(E.KiCadObjectType.KOT_SCH_SYMBOL)

    refs = []
    for item in s.client.call(q, ed.GetItemsResponse).items:
        sym = st.SchematicSymbolInstance()
        item.Unpack(sym)
        refs.append(sym.reference_field.text.text)

    assert "CT1" in refs, refs


def test_pin_positions_are_absolute(eeschema_session):
    """PackSymbol reports sheet coordinates, not symbol space. Applying the
    (1, 0, 0, -1) transform on top lands at twice the symbol's x -- which is what
    the first version of pin_positions() did."""
    s = eeschema_session
    sym = s.place_symbol("Device:R", 100, 50, reference="RT2")

    xs = {round(x, 2) for x, _, _ in s.pin_positions(sym).values()}
    ys = sorted(round(y, 2) for _, y, _ in s.pin_positions(sym).values())

    assert xs == {100.0}, xs                 # not 200.0
    assert ys == [46.19, 53.81], ys          # +/- 3.81 mm about the origin


def test_rotation_turns_the_symbol(eeschema_session):
    """A rotated resistor's pins go horizontal; an unrotated one's stay vertical."""
    s = eeschema_session

    upright = s.place_symbol("Device:R", 140, 50, reference="RT3")
    turned = s.place_symbol("Device:R", 170, 50, reference="RT4", rotation=90)

    up_x = {round(x, 2) for x, _, _ in s.pin_positions(upright).values()}
    tn_y = {round(y, 2) for _, y, _ in s.pin_positions(turned).values()}

    assert len(up_x) == 1, "unrotated pins should share an x"
    assert len(tn_y) == 1, "rotated pins should share a y"


def test_rotation_must_be_a_right_angle(eeschema_session):
    """KiCad only has four orientations; catch it client-side with a clear message
    rather than sending SSO_UNKNOWN and silently getting 0 degrees."""
    s = eeschema_session

    with pytest.raises(ValueError, match="rotation must be one of"):
        s.place_symbol("Device:R", 200, 50, rotation=45)


def test_multi_unit_symbol_places_the_requested_unit(eeschema_session):
    """An LM324 has four op-amp units plus a power unit; unit 2 is pins 5, 6 and 7."""
    s = eeschema_session
    sym = s.place_symbol("Amplifier_Operational:LM324", 60, 90, reference="UT1", unit=2)

    assert sorted(s.pin_positions(sym)) == ["5", "6", "7"], s.pin_positions(sym)


def test_wires_and_labels_connect_a_net(eeschema_session):
    """End to end, and checked electrically rather than visually: place a part, wire
    its pin to a power symbol, and confirm the netlist reports the net."""
    from autokicad.ipc import protos

    s = eeschema_session
    r = s.place_symbol("Device:R", 100, 130, reference="RT9")
    pins = sorted(s.pin_positions(r).items(), key=lambda kv: kv[1][1])
    (_, (px, py, _)) = pins[0]

    pwr = s.place_symbol("power:+3V3", px, py - 12.7, reference="#PWRT1")
    (qx, qy, _) = next(iter(s.pin_positions(pwr).values()))
    s.wire(px, py, qx, qy)

    sc = protos.schematic_commands()
    q = sc.GetSchematicNetlist()
    q.document.CopyFrom(s.document)
    names = {n.name for n in s.client.call(q, sc.SchematicNetlistResponse).nets}

    assert "+3V3" in names, sorted(n for n in names if not n.startswith("unconnected-"))


# ---- ERC -----------------------------------------------------------------


def test_erc_runs_and_reports_nothing_on_an_empty_sheet(eeschema_session):
    """A sheet with a couple of unwired parts still has violations; what matters
    here is that ERC runs at all and answers with a structured report."""
    s = eeschema_session
    r = s.run_erc()

    assert r.units == 2, r.units          # U_MM after the default is applied
    assert list(r.included_severities), "no severities echoed back"


def test_erc_finds_unconnected_pins(eeschema_session):
    """The check that matters for an agent: a placed-but-unwired part is reported.
    Cross-checks against the netlist, which counts the same pins independently."""
    from autokicad.ipc import protos

    s = eeschema_session
    s.place_symbol("Device:R", 240, 40, reference="RE1")

    r = s.run_erc()
    unconnected = [v for v in r.violations if v.type == "pin_not_connected"]
    assert unconnected, [v.type for v in r.violations]

    sc = protos.schematic_commands()
    q = sc.GetSchematicNetlist()
    q.document.CopyFrom(s.document)
    nets = s.client.call(q, sc.SchematicNetlistResponse).nets
    dangling = [n for n in nets if n.name.startswith("unconnected-")]

    assert len(unconnected) <= len(dangling) + 2, (len(unconnected), len(dangling))


def test_erc_violations_carry_a_sheet_path(eeschema_session):
    """ERC groups per sheet, unlike DRC. On a hierarchical design the same check
    firing on two sheets is two different problems, so the sheet has to survive
    the flattening into a single violation list."""
    s = eeschema_session
    r = s.run_erc()

    assert r.violations, "expected at least one violation to inspect"
    assert all(v.sheet_path for v in r.violations)
    assert all(v.sheet_uuid_path for v in r.violations)


def test_erc_positions_are_nanometres(eeschema_session):
    """Regression: FromUserUnit returns *internal* units and a schematic IU is 100 nm,
    not 1 nm. Storing that straight into x_nm -- which is correct on the board side
    only because a pcbnew IU happens to be a nanometre -- reported every ERC
    coordinate 100x too small."""
    s = eeschema_session
    r = s.run_erc()

    positioned = [v for v in r.violations if v.items and v.items[0].position.x_nm]
    assert positioned, "no violation carried a position"

    # Anything on a real sheet is at least a few hundred micrometres from the origin;
    # the 100x-too-small bug put these in the tens of thousands of nanometres.
    assert max(v.items[0].position.x_nm for v in positioned) > 100_000


def test_erc_reports_ignored_checks(eeschema_session):
    """So a caller can tell "no violations" from "that check was switched off"."""
    s = eeschema_session
    r = s.run_erc()

    assert r.ignored_checks, "expected the default ERC config to disable something"
    assert all(c.key for c in r.ignored_checks)
