#!/usr/bin/env python3
"""
build_pcb.py - generates Fascia.kicad_pcb from Fascia.kicad_sch.

Same script-driven discipline as the sibling projects: this script is the
source of truth, never the generated .kicad_pcb. Change this and re-run.

Ground truth for what goes on the board comes from two places, neither of
which is this script's own idea of the design:

  - the parts list is read out of the schematic file
  - the net assignments come from kicad-cli's own netlist export

so drift between schematic and PCB shows up as a hard failure here rather
than as a quietly wrong board.

Current stage: parts placed, board outlined, nets assigned. Nothing is
routed yet - route_board.py is the next step, as in the sibling projects.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import uuid as uuid_module

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position
from kiutils.items.brditems import LayerToken
from kiutils.items.gritems import GrLine

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SCH = os.path.join(HERE, "Fascia.kicad_sch")
PCB = os.path.join(HERE, "Fascia.kicad_pcb")
KICAD_FOOTPRINTS = os.path.join("C:" + os.sep, "Program Files", "KiCad",
                                "10.0", "share", "kicad", "footprints")
PROJECT_FOOTPRINTS = os.path.join(HERE, "footprints")


def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    for c in (os.path.join("C:" + os.sep, "Program Files", "KiCad", v, "bin",
                           "kicad-cli.exe") for v in ("10.0", "9.0", "8.0")):
        if os.path.isfile(c):
            return c
    return None


KICAD_CLI = find_kicad_cli()
if not KICAD_CLI:
    raise SystemExit("kicad-cli not found - needed for netlist export and "
                     "pcb upgrade")


def U():
    return str(uuid_module.uuid4())


# ---------------------------------------------------------------------------
# 1. Parts from the schematic, nets from kicad-cli's netlist export
# ---------------------------------------------------------------------------
from kiutils.schematic import Schematic          # noqa: E402
from kiutils.utils import sexpr                  # noqa: E402

sch = Schematic.from_sexpr(sexpr.parse_sexp(open(SCH, encoding="utf-8").read()))

parts = {}      # ref -> {"footprint", "value"}
for inst in sch.schematicSymbols:
    ref = next((p.value for p in inst.properties if p.key == "Reference"), "")
    if not ref or ref.startswith("#"):
        continue                    # power symbols and flags are not parts
    fp = next((p.value for p in inst.properties if p.key == "Footprint"), "")
    val = next((p.value for p in inst.properties if p.key == "Value"), "")
    # A multi-unit symbol appears once per unit; it is still one part.
    parts.setdefault(ref, {"footprint": fp, "value": val})

NET_PATH = os.path.join(os.environ.get("TEMP", HERE), "fascia_netlist.net")
r = subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format",
                    "kicadsexpr", "--output", NET_PATH, SCH],
                   capture_output=True, text=True)
if r.returncode != 0:
    raise SystemExit(f"netlist export failed: {r.stderr}")

pad_net = {}    # (ref, pad number) -> net name
net_names = []
for block in re.split(r"\(net\s", open(NET_PATH, encoding="utf-8").read())[1:]:
    m = re.search(r'\(name "([^"]+)"\)', block)
    if not m:
        continue
    name = m.group(1).lstrip("/")
    nodes = re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', block)
    if len(nodes) < 2:
        continue                    # single-node nets carry no connectivity
    net_names.append(name)
    for ref, pin in nodes:
        pad_net[(ref, pin)] = name

print(f"{len(parts)} parts, {len(net_names)} nets from the schematic")

missing = sorted(ref for ref, p in parts.items() if not p["footprint"])
if missing:
    raise SystemExit(f"parts with no footprint assigned: {missing}")


# ---------------------------------------------------------------------------
# 2. Footprints
# ---------------------------------------------------------------------------
_fp_cache = {}
_fp_props = {}   # lib:name -> the property names the LIBRARY file has


def load_footprint(lib_colon_name):
    if lib_colon_name not in _fp_cache:
        lib, _, name = lib_colon_name.partition(":")
        rel = os.path.join(f"{lib}.pretty", f"{name}.kicad_mod")
        for root in (PROJECT_FOOTPRINTS, KICAD_FOOTPRINTS):
            path = os.path.join(root, rel)
            if os.path.isfile(path):
                _fp_cache[lib_colon_name] = path
                # Record exactly which properties the library file carries.
                # kiutils adds a Description on write even when the source
                # has none, and that single extra property is enough for
                # DRC to report lib_footprint_mismatch against a footprint
                # that is otherwise byte-for-byte the library's.
                txt = open(path, encoding="utf-8").read()
                _fp_props[lib_colon_name] = set(
                    re.findall(r'\(property "([^"]+)"', txt))
                break
        else:
            raise FileNotFoundError(f"footprint not found: {lib_colon_name}")
    fp = Footprint.from_file(_fp_cache[lib_colon_name])
    fp.libId = lib_colon_name
    return fp


def footprint_bbox(fp):
    """
    Extent in the footprint's own frame, from pads AND graphics.

    Pads alone undersell parts whose silkscreen or courtyard sticks out
    past the copper - this project's notes record real DRC failures from
    a pad-only bbox on a connector with a body outline.
    """
    xs, ys = [], []
    for pad in fp.pads:
        x, y = pad.position.X, pad.position.Y
        w, h = pad.size.X / 2, pad.size.Y / 2
        xs += [x - w, x + w]
        ys += [y - h, y + h]
    for g in fp.graphicItems:
        for attr in ("start", "end", "center", "position"):
            p = getattr(g, attr, None)
            if p is not None and hasattr(p, "X"):
                xs.append(p.X)
                ys.append(p.Y)
        for p in getattr(g, "coordinates", []) or []:
            xs.append(p.X)
            ys.append(p.Y)
    if not xs:
        return (-1, -1, 1, 1)
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# 3. Placement
# ---------------------------------------------------------------------------
MARGIN = 4.0        # board edge to nearest part
GAP = 1.2           # between parts
ROW_GAP = 2.0

loaded = {ref: load_footprint(p["footprint"]) for ref, p in parts.items()}
boxes = {ref: footprint_bbox(fp) for ref, fp in loaded.items()}


def size(ref):
    x0, y0, x1, y1 = boxes[ref]
    return (x1 - x0, y1 - y0)


# The Verdin socket dominates the board and has to sit at an edge so the
# module can overhang; place it first and alone on the top row.
SOCKET = "J1"
others = sorted((r for r in parts if r != SOCKET),
                key=lambda r: -size(r)[1])

sock_w, sock_h = size(SOCKET)
rows = []                    # [(y_top, height, [refs])]
row, row_w, row_h = [], 0.0, 0.0
MAX_W = max(sock_w + 2 * MARGIN, 150.0)

for ref in others:
    w, h = size(ref)
    if row and row_w + GAP + w > MAX_W - 2 * MARGIN:
        rows.append((row, row_w, row_h))
        row, row_w, row_h = [], 0.0, 0.0
    row_w = row_w + (GAP if row else 0.0) + w
    row_h = max(row_h, h)
    row.append(ref)
if row:
    rows.append((row, row_w, row_h))

board_w = round(MAX_W, 2)
board_h = round(MARGIN + sock_h + ROW_GAP
                + sum(rh + ROW_GAP for _, _, rh in rows) + MARGIN, 2)

placement = {}
y = MARGIN
# socket centred on the top row
placement[SOCKET] = (round(board_w / 2, 2), round(y + sock_h / 2, 2))
y += sock_h + ROW_GAP
for refs, rw, rh in rows:
    x = MARGIN
    for ref in refs:
        w, h = size(ref)
        placement[ref] = (round(x + w / 2, 2), round(y + rh / 2, 2))
        x += w + GAP
    y += rh + ROW_GAP

print(f"board {board_w} x {board_h} mm, {len(rows) + 1} rows")


# ---------------------------------------------------------------------------
# 4. Board
# ---------------------------------------------------------------------------
board = Board.create_new()
# Six layers: the DSI and LVDS pairs need a reference plane next to them,
# and the audio section wants its own quiet return.
for i, nm in enumerate(("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu"), start=1):
    board.layers.insert(i, LayerToken(ordinal=i, name=nm, type="signal"))

board.nets = [Net(0, "")]
net_num = {}
for i, name in enumerate(sorted(set(net_names)), start=1):
    net_num[name] = i
    board.nets.append(Net(i, name))

ref_label_pos = {}
for ref, p in sorted(parts.items()):
    fp = loaded[ref]
    x, y = placement[ref]
    bx0, by0, bx1, by1 = boxes[ref]
    # Footprint origin is not its bbox centre; offset so the BBOX lands
    # where the packer put it.
    ox = x - (bx0 + bx1) / 2
    oy = y - (by0 + by1) / 2
    fp.position = Position(round(ox, 3), round(oy, 3), 0)
    fp.uuid = U()
    fp.properties["Reference"] = ref
    fp.properties["Value"] = p["value"]
    # Library-generator metadata has no meaning on a board and, left as a
    # bare property, `pcb upgrade` renders it visible at (0,0) on F.Fab.
    fp.properties.pop("KiLib_Generator", None)
    ref_label_pos[ref] = round(by0 - 0.6, 3)

    hit = 0
    for pad in fp.pads:
        # Real downloaded footprints can use KiCad's legacy unquoted pad
        # numbers, which kiutils parses as int; always normalise before
        # using one as a lookup key.
        num = str(pad.number)
        name = pad_net.get((ref, num))
        if name:
            pad.net = Net(net_num[name], name)
            hit += 1
    board.footprints.append(fp)

# Measure the real extent of everything placed, rather than trusting the
# predicted board size. The packer works in bounding boxes; if any bbox
# understates a footprint the prediction is quietly wrong, and the first
# symptom is parts sitting outside the board edge - which DRC does not
# report, because an unrouted board has no copper crossing the edge yet.
ex0 = ey0 = 1e9
ex1 = ey1 = -1e9
for ref in parts:
    bx0, by0, bx1, by1 = boxes[ref]
    fx, fy = loaded[ref].position.X, loaded[ref].position.Y
    ex0 = min(ex0, fx + bx0)
    ey0 = min(ey0, fy + by0)
    ex1 = max(ex1, fx + bx1)
    ey1 = max(ey1, fy + by1)

pred_w, pred_h = board_w, board_h
board_w = round(ex1 - ex0 + 2 * MARGIN, 2)
board_h = round(ey1 - ey0 + 2 * MARGIN, 2)
if abs(board_w - pred_w) > 0.5 or abs(board_h - pred_h) > 0.5:
    print(f"  note: predicted {pred_w} x {pred_h}, measured "
          f"{board_w} x {board_h} - using the measured size")

board_x0 = round(ex0 - MARGIN, 2)
board_y0 = round(ey0 - MARGIN, 2)
outline = [(board_x0, board_y0), (board_x0 + board_w, board_y0),
           (board_x0 + board_w, board_y0 + board_h),
           (board_x0, board_y0 + board_h), (board_x0, board_y0)]
for a, b in zip(outline, outline[1:]):
    board.graphicItems.append(GrLine(
        start=Position(a[0], a[1]), end=Position(b[0], b[1]),
        layer="Edge.Cuts", width=0.1, tstamp=U()))

board.to_file(PCB)

# `pcb upgrade` fills in any property KiCad's writer left bare using its
# own per-name default - and for Reference that default is visible, at
# (0,0,0), on F.SilkS, i.e. dead centre on the part's own pads. Patch the
# bare tokens to real positions BEFORE upgrade runs, while their exact
# one-line form is still known.
txt = open(PCB, encoding="utf-8").read()
for ref, dy in ref_label_pos.items():
    txt = txt.replace(
        f'(property "Reference" "{ref}")',
        f'(property "Reference" "{ref}" (at 0 {dy} 0) (layer "F.SilkS") '
        f'(effects (font (size 0.8 0.8) (thickness 0.12))))')
open(PCB, "w", encoding="utf-8").write(txt)

# The exposed-pad footprints this board uses (TI's DRB0008A) carry 0.2 mm
# thermal vias inside the pad, below KiCad's default 0.3 mm minimum hole.
# Those vias are worth having - they are how a regulator's heat reaches
# the inner planes - so the constraint is relaxed deliberately rather than
# the footprint being swapped for one without them. 0.2 mm is within
# ordinary fab capability; it is not an exotic requirement.
PRO = os.path.join(HERE, "Fascia.kicad_pro")
if os.path.exists(PRO):
    with open(PRO, encoding="utf-8") as f:
        pro = json.load(f)
    rules = pro.setdefault("board", {}).setdefault("design_settings", {}) \
               .setdefault("rules", {})
    rules["min_through_hole_diameter"] = 0.2
    rules["min_hole_to_hole"] = 0.25
    with open(PRO, "w", encoding="utf-8") as f:
        json.dump(pro, f, indent=2)

r = subprocess.run([KICAD_CLI, "pcb", "upgrade", PCB],
                   capture_output=True, text=True)
if r.returncode != 0:
    raise SystemExit(f"pcb upgrade failed: {r.stderr}")

placed = len(board.footprints)
netted = sum(1 for fp in board.footprints for pad in fp.pads
             if pad.net and pad.net.number)
print(f"wrote {os.path.basename(PCB)}: {placed} footprints, "
      f"{netted} pads netted, {len(board.nets) - 1} nets, 6 layers")
print(f"  board {board_w} x {board_h} mm")

# Everything must be inside the outline. Cheap to check, and the failure
# it catches (a part hanging off the edge) is invisible to DRC on a board
# with no routing yet.
outside = []
for ref in parts:
    bx0, by0, bx1, by1 = boxes[ref]
    fx, fy = loaded[ref].position.X, loaded[ref].position.Y
    if (fx + bx0 < board_x0 or fy + by0 < board_y0
            or fx + bx1 > board_x0 + board_w
            or fy + by1 > board_y0 + board_h):
        outside.append(ref)
if outside:
    raise SystemExit(f"parts outside the board outline: {sorted(outside)}")
print(f"  all {len(parts)} parts inside the outline")
