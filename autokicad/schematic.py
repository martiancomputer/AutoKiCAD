"""Draw a schematic in a live eeschema, over the IPC API.

Everything here goes through the API rather than synthetic input, which is what
makes it safe to point at a real desktop: no clicks land on whatever window
happens to be under the pointer.

Coordinates are millimetres. The proto carries nanometres, so they are converted
on the way in -- schematic sheets are laid out in millimetres and asking callers
to think in nanometres invites the off-by-1e6 that the DRC work already hit once.

Symbol placement relies on the server resolving a bare LIB_ID against the
configured libraries. Before that fix, sending a definition with only an id
produced a symbol with no body and no pins: present in the file, invisible on
screen. See documentation/03-gaps.md.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ipc import ApiClient, protos

MM = 1_000_000  # nanometres per millimetre

REPO = Path(__file__).resolve().parents[1]
LIVE_GUI = REPO / "documentation" / "tools" / "live-gui.sh"


def mm(value: float) -> int:
    return int(round(value * MM))


@dataclass
class SchematicSession:
    """A connection to an open schematic in a running eeschema."""

    client: ApiClient
    document: object
    pace: float = 0.0          # seconds between edits, so a human can follow along
    heartbeat: bool = True     # keep live-gui.sh's watchdog from stopping the recording
    created: list = field(default_factory=list)

    @classmethod
    def connect(cls, socket: str | None = None, **kwargs) -> SchematicSession:
        if socket:
            from .ipc.transport import PynngTransport
            client = ApiClient(transport=PynngTransport(url=socket))
        else:
            client = ApiClient()
        docs = client.open_documents(protos.base_types().DocumentType.DOCTYPE_SCHEMATIC)

        if not docs:
            raise RuntimeError("eeschema has no schematic open")

        return cls(client=client, document=docs[0], **kwargs)

    # ---- plumbing --------------------------------------------------------

    def _beat(self) -> None:
        if not self.heartbeat or not LIVE_GUI.is_file():
            return
        try:
            subprocess.run([str(LIVE_GUI), "beat"], check=False, capture_output=True, timeout=10)
        except Exception:
            pass  # the watchdog is a safety net, never a reason to fail an edit

    def _create(self, *packed):
        """CreateItems with one or more already-packed Any messages."""
        ed = protos.editor_commands()
        req = ed.CreateItems()
        req.header.document.CopyFrom(self.document)

        for item in packed:
            req.items.add().CopyFrom(item)

        resp = self.client.call(req, ed.CreateItemsResponse)

        # ItemCreationResult.status is an ItemStatus *message*, not the enum -- the code
        # lives one level down.
        failed = [r for r in resp.created_items if r.status.code != ed.ISC_OK]
        if failed:
            raise RuntimeError(f"CreateItems rejected {len(failed)} item(s): {failed[0]}")

        self.created.extend(resp.created_items)
        self._beat()

        if self.pace:
            time.sleep(self.pace)

        return resp

    @staticmethod
    def _pack(msg):
        from google.protobuf.any_pb2 import Any
        any_msg = Any()
        any_msg.Pack(msg)
        return any_msg

    # ---- drawing ---------------------------------------------------------

    def place_symbol(self, lib_id: str, x: float, y: float,
                     reference: str | None = None, value: str | None = None,
                     unit: int = 1, body_style: int = 1,
                     rotation: int = 0, mirror_x: bool = False, mirror_y: bool = False):
        """Place `lib_id` (e.g. "Device:R") at (x, y) millimetres.

        Uses the PlaceSymbol command, which resolves the LIB_ID server-side and builds
        the symbol through the same constructor the interactive placement tool uses.

        CreateItems with a SchematicSymbolInstance is deliberately *not* used here: it
        requires the caller to supply the whole definition, and sending only a LIB_ID
        yields a symbol whose body draws but whose pins land off-sheet, because the
        library symbol's draw items end up in instance space rather than symbol space.
        """
        sc = protos.schematic_commands()
        nickname, _, name = lib_id.partition(":")

        req = sc.PlaceSymbol()
        req.schematic.CopyFrom(self.document)
        req.lib_id.library_nickname = nickname
        req.lib_id.entry_name = name
        req.position.x_nm = mm(x)
        req.position.y_nm = mm(y)
        req.unit = unit
        req.body_style = body_style

        if reference is not None:
            req.reference = reference
        if value is not None:
            req.value = value

        st = protos.schematic_types()
        orientations = {0: st.SSO_0, 90: st.SSO_90, 180: st.SSO_180, 270: st.SSO_270}

        if rotation not in orientations:
            raise ValueError(f"rotation must be one of {sorted(orientations)}, got {rotation}")

        if rotation or mirror_x or mirror_y:
            req.transform.orientation = orientations[rotation]
            req.transform.mirror_x = mirror_x
            req.transform.mirror_y = mirror_y

        resp = self.client.call(req, sc.PlaceSymbolResponse)
        self.created.append(resp.symbol)
        self._beat()

        if self.pace:
            time.sleep(self.pace)

        return resp.symbol

    def search_symbols(self, query: str = "", libraries: list[str] | None = None, *,
                       max_results: int = 0, match_all_terms: bool = False,
                       power_only: bool = False):
        """Search the configured symbol libraries, returning the raw response.

        There is no symbol equivalent of the footprint info cache, so an unscoped
        search reads every configured library. Pass `libraries` when you know where
        to look.
        """
        sc = protos.schematic_commands()
        req = sc.SearchSymbols()
        req.schematic.CopyFrom(self.document)
        req.query = query
        req.max_results = max_results
        req.match_all_terms = match_all_terms
        req.power_only = power_only
        req.libraries.extend(libraries or [])

        return self.client.call(req, sc.SearchSymbolsResponse)

    def run_erc(self, units: str = "mm", severities: list[int] | None = None):
        """Run ERC on the open schematic, returning the raw response.

        Like the DRC counterpart, this sees the schematic as it currently is in the
        editor, unsaved edits included; `kicad-cli sch erc` can only read the last
        saved file.
        """
        sc = protos.schematic_commands()
        E = protos.enums()
        req = sc.RunErc()
        req.schematic.CopyFrom(self.document)
        req.units = {"mm": E.Units.U_MM, "in": E.Units.U_INCH,
                     "mils": E.Units.U_MILS}[units]

        if severities:
            req.severities.extend(severities)

        return self.client.call(req, sc.RunErcResponse)

    def pin_positions(self, symbol) -> dict:
        """Absolute pin positions in millimetres, keyed by pin number.

        PackSymbol reports pins in *sheet* coordinates for a placed symbol, not in
        symbol space, so no transform is applied here. (Symbol-space coordinates are
        what the library file stores -- 3V3 at (0, 40.64) for an ESP32-S3-WROOM-1 --
        and applying the (1, 0, 0, -1) transform on top of an already-absolute
        position lands you at twice the symbol's x.)

        Each value is (x, y, name). Several pins commonly share a point -- a module's
        GND pins are usually stacked -- so key by number, not name.
        """
        st = protos.schematic_types()
        out = {}

        for child in symbol.definition.items:
            if "SchematicPin" not in child.item.type_url:
                continue

            pin = st.SchematicPin()
            child.item.Unpack(pin)
            out[pin.number] = (
                pin.position.x_nm / MM,
                pin.position.y_nm / MM,
                pin.name,
            )

        return out

    def pins_named(self, symbol, name: str) -> list:
        """Every (number, x, y) whose pin name matches, case-insensitively."""
        return [
            (num, x, y)
            for num, (x, y, nm) in self.pin_positions(symbol).items()
            if nm.upper() == name.upper()
        ]

    def wire(self, x1: float, y1: float, x2: float, y2: float):
        """A wire segment between two points, in millimetres."""
        st = protos.schematic_types()
        line = st.SchematicLine()
        line.start.x_nm = mm(x1)
        line.start.y_nm = mm(y1)
        line.end.x_nm = mm(x2)
        line.end.y_nm = mm(y2)
        line.type = st.SLT_WIRE
        self._create(self._pack(line))
        return line

    def bus(self, x1: float, y1: float, x2: float, y2: float):
        st = protos.schematic_types()
        line = st.SchematicLine()
        line.start.x_nm = mm(x1)
        line.start.y_nm = mm(y1)
        line.end.x_nm = mm(x2)
        line.end.y_nm = mm(y2)
        line.type = st.SLT_BUS
        self._create(self._pack(line))
        return line

    def label(self, text: str, x: float, y: float, glob: bool = False):
        """A net label. `glob` selects a global label over a local one."""
        st = protos.schematic_types()
        lbl = st.GlobalLabel() if glob else st.LocalLabel()
        lbl.position.x_nm = mm(x)
        lbl.position.y_nm = mm(y)
        lbl.text.text = text
        self._create(self._pack(lbl))
        return lbl

    def junction(self, x: float, y: float):
        st = protos.schematic_types()
        j = st.Junction()
        j.position.x_nm = mm(x)
        j.position.y_nm = mm(y)
        self._create(self._pack(j))
        return j

    def close(self) -> None:
        self.client.close()


# ---- demo composition ----------------------------------------------------


def compose_demo(session: SchematicSession, origin: tuple[float, float] = (60.0, 60.0)) -> None:
    """Draw a small RC + LED sheet: symbols, wires, labels, junctions.

    Deliberately cosmetic. It exercises every primitive the agent-facing API has
    for schematics rather than trying to be a correct circuit.
    """
    ox, oy = origin

    session.label("VCC", ox, oy - 12, glob=True)
    session.wire(ox, oy - 12, ox, oy - 4)

    session.place_symbol("Device:R", ox, oy, reference="R1", value="10k")
    session.wire(ox, oy + 4, ox, oy + 18)

    session.place_symbol("Device:LED", ox, oy + 24, reference="D1", value="RED")
    session.wire(ox, oy + 28, ox, oy + 40)

    # Decoupling branch off the midpoint.
    session.junction(ox, oy + 18)
    session.wire(ox, oy + 18, ox + 32, oy + 18)
    session.place_symbol("Device:C", ox + 32, oy + 24, reference="C1", value="100n")
    session.wire(ox + 32, oy + 18, ox + 32, oy + 20)
    session.wire(ox + 32, oy + 28, ox + 32, oy + 40)

    # Common ground rail.
    session.wire(ox, oy + 40, ox + 32, oy + 40)
    session.junction(ox, oy + 40)
    session.label("GND", ox + 16, oy + 44, glob=True)

    # A second stage, so the sheet looks like a schematic rather than one part.
    session.place_symbol("Device:R", ox + 64, oy, reference="R2", value="4k7")
    session.place_symbol("Device:C", ox + 64, oy + 24, reference="C2", value="1u")
    session.wire(ox + 64, oy + 4, ox + 64, oy + 20)
    session.wire(ox + 64, oy + 28, ox + 64, oy + 40)
    session.wire(ox + 32, oy + 40, ox + 64, oy + 40)
    session.label("OUT", ox + 64, oy - 12, glob=True)
    session.wire(ox + 64, oy - 12, ox + 64, oy - 4)
