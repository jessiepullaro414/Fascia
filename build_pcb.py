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
        # "mid" matters: an fp_arc is stored as start/mid/end, and an arc
        # that bulges outward has its extreme at the mid point. Leaving it
        # out understates parts drawn with curved outlines - which is how
        # the coin-cell holder ended up overlapping its neighbour while
        # this function reported them clear.
        for attr in ("start", "end", "center", "position", "mid"):
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
#
# This is the same skyline bottom-left packer manifold-pcb and thermo-pcb
# use, ported rather than reinvented. Two properties matter here:
#
#   - every part is tried BOTH unrotated and rotated 90 degrees, and goes
#     wherever it yields the lowest resulting top edge. That is what turns
#     the tall pin-header connectors on their sides and lets the 0603s
#     backfill the low spots beside the big parts, instead of each part
#     being stuck in a same-size block.
#
#   - the keepout is a real packer input, not a check afterwards.
#
# On top of that, parts are sorted by functional group so a block's own
# passives stay near their IC. Group membership is derived from the
# netlist - each part joins whichever IC or connector it shares the most
# NON-RAIL nets with. Rails are excluded because GND touches everything
# and would make every part look equally related to everything.
# ---------------------------------------------------------------------------
MARGIN = 4.0        # board edge to nearest part
CLEAR = 1.3         # clearance folded into each part's reserved footprint

loaded = {ref: load_footprint(p["footprint"]) for ref, p in parts.items()}
raw_boxes = {ref: footprint_bbox(fp) for ref, fp in loaded.items()}

# --- functional grouping, derived from the netlist --------------------------
nets_of = {}
for (ref, _pin), net in pad_net.items():
    nets_of.setdefault(ref, set()).add(net)

RAILS = {"GND", "+5V", "+3V3", "+1V8", "+12V_PROT", "VBAT_F"}
anchors = [r for r in parts if r[0] in "UJ"]
group_of = {}
for ref in parts:
    if ref in anchors:
        group_of[ref] = ref
        continue
    mine = nets_of.get(ref, set()) - RAILS
    best, score = None, 0
    for a in anchors:
        n = len(mine & (nets_of.get(a, set()) - RAILS))
        if n > score:
            best, score = a, n
    group_of[ref] = best or "U2"

# Group order: power in, front end, regulators, then the blocks that hang
# off the module, then the debug and battery corner.
ORDER = ["J1", "J2", "U1", "U2", "U3", "U6", "U5", "J4", "U7", "U8",
         "U9", "J5", "J6", "U4", "J3", "U10", "J7", "J8", "J9", "J10"]
gi = {a: i for i, a in enumerate(ORDER)}
group_rank = {ref: gi.get(group_of[ref], len(ORDER)) for ref in parts}


def rotated_bbox(bb, angle):
    """Bounding box after KiCad's (x, y) -> (y, -x) rotation for 90 deg."""
    x0, y0, x1, y1 = bb
    if angle % 360 == 90:
        return (y0, -x1, y1, -x0)
    if angle % 360 == 180:
        return (-x1, -y1, -x0, -y0)
    if angle % 360 == 270:
        return (-y1, x0, -y0, x1)
    return bb


def skyline_pack(refs, max_width, margin, sort_key, initial_skyline):
    sized = []
    for ref in refs:
        x0, y0, x1, y1 = raw_boxes[ref]
        sized.append((ref, x1 - x0, y1 - y0, x0, y0, x1, y1))
    sized.sort(key=sort_key)

    skyline = list(initial_skyline)

    def profile_height(x, w):
        h = 0.0
        for sx, sw, sh in skyline:
            if sx + sw <= x + 1e-9 or sx >= x + w - 1e-9:
                continue
            h = max(h, sh)
        return h

    def best_position(w):
        best = None
        cand = set()
        for sx, sw, _sh in skyline:
            cand.add(sx)
            cand.add(sx + sw - w)      # right-justify against this segment
        for x in cand:
            if x < -1e-9 or x + w > max_width + 1e-9:
                continue
            y = profile_height(x, w)
            if best is None or (y, x) < (best[0], best[1]):
                best = (y, x)
        return best

    def update_skyline(x, w, top):
        x_end = x + w
        segs = []
        for sx, sw, sh in skyline:
            s_end = sx + sw
            if s_end <= x + 1e-9 or sx >= x_end - 1e-9:
                segs.append((sx, sw, sh))
                continue
            if sx < x:
                segs.append((sx, x - sx, sh))
            if s_end > x_end:
                segs.append((x_end, s_end - x_end, sh))
        segs.append((x, w, top))
        segs.sort(key=lambda t: t[0])
        merged = []
        for seg in segs:
            if merged and abs(merged[-1][0] + merged[-1][1] - seg[0]) < 1e-6 \
                    and abs(merged[-1][2] - seg[2]) < 1e-6:
                merged[-1] = (merged[-1][0], merged[-1][1] + seg[1],
                              merged[-1][2])
            else:
                merged.append(seg)
        return merged

    placed, rotated = {}, set()
    for ref, w, h, x0, y0, x1, y1 in sized:
        options = []
        p0 = best_position(w + margin)
        if p0 is not None:
            options.append((p0[0] + h + margin, p0[1], False))
        p90 = best_position(h + margin)
        if p90 is not None:
            options.append((p90[0] + w + margin, p90[1], True))
        if not options:
            raise RuntimeError(f"{ref} ({w:.1f}x{h:.1f}) does not fit in "
                               f"{max_width:.1f} mm even alone")
        options.sort(key=lambda o: (o[0], o[1]))
        top, x, is_rot = options[0]
        if is_rot:
            rw, rh = h + margin, w + margin
            skyline = update_skyline(x, rw, top)
            # KiCad's "at x y 90" rotates local (x,y) -> (y,-x), so the
            # bbox corners map to (y0,-x1)-(y1,-x0) and the local origin
            # lands at (y0,-x1). Confirmed empirically in the sibling
            # projects against real DRC output - the opposite assumption
            # put a rotated header's far pads against its neighbour.
            placed[ref] = (x - y0, (top - rh) + x1)
            rotated.add(ref)
        else:
            rw, rh = w + margin, h + margin
            skyline = update_skyline(x, rw, top)
            placed[ref] = (x - x0, (top - rh) - y0)

    used_w = used_h = 0.0
    for ref, w, h, x0, y0, x1, y1 in sized:
        px, py = placed[ref]
        if ref in rotated:
            used_w = max(used_w, px + y1)
            used_h = max(used_h, py - x0)
        else:
            used_w = max(used_w, px + x1)
            used_h = max(used_h, py + y1)
    return placed, rotated, used_w, used_h


BOARD_W = 150.0
INNER = BOARD_W - 2 * MARGIN

origin, rotated_refs, used_w, used_h = skyline_pack(
    list(parts), INNER, CLEAR,
    # Big parts first regardless of group, then everything else grouped.
    # Pure group order buried the coin-cell holder at the end, where it
    # landed in an empty row of its own and set the board height; pure
    # area order scatters each IC's passives away from it. This gets the
    # large awkward parts placed while the skyline is still free, then
    # keeps the small stuff with its own IC.
    sort_key=lambda t: (0 if t[1] * t[2] > 90.0 else 1,
                        0 if t[1] * t[2] > 90.0 else group_rank[t[0]],
                        -(t[1] * t[2])),
    initial_skyline=[(0.0, INNER, 0.0)])

angles = {ref: 90.0 for ref in rotated_refs}
placement = {ref: (round(MARGIN + x, 3), round(MARGIN + y, 3))
             for ref, (x, y) in origin.items()}

board_w = BOARD_W
board_h = round(used_h + 2 * MARGIN, 2)
print(f"packed {len(parts)} parts, {len(rotated_refs)} rotated 90 deg; "
      f"used {used_w:.1f} x {used_h:.1f} mm")


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
    # The packer returns the footprint ORIGIN directly, already
    # accounting for the bbox offset and for rotation.
    ox, oy = placement[ref]
    ang = angles.get(ref, 0.0)
    fp.position = Position(round(ox, 3), round(oy, 3), ang)
    if ang:
        # A rotated footprint's pad SHAPES do not follow the parent's
        # angle the way pad positions do, so each pad needs its angle set
        # explicitly or KiCad computes clearances against unrotated pad
        # outlines and reports phantom shorts.
        #
        # The refinement this board forced: the angle must be ADDED to
        # whatever the pad already had, not assigned. This project's notes
        # describe assigning the placement angle directly, which is right
        # only for pads with no angle of their own. Every pad in the
        # SODIMM-260 footprint already carries 270 degrees - the edge
        # fingers are oriented - and overwriting that with 90 turned
        # 0.3 x 1.7 mm pads at 0.5 mm pitch sideways into each other:
        # 820 shorting and clearance violations, all J1 against itself.
        for pad in fp.pads:
            pad.position.angle = ((pad.position.angle or 0.0) + ang) % 360
    fp.uuid = U()
    fp.properties["Reference"] = ref
    fp.properties["Value"] = p["value"]
    # Library-generator metadata has no meaning on a board and, left as a
    # bare property, `pcb upgrade` renders it visible at (0,0) on F.Fab.
    fp.properties.pop("KiLib_Generator", None)
    # Label above the part. A lane-assignment pass (trying above, then
    # below, then further out, checking against neighbours) was tried and
    # measured WORSE - 14 silkscreen findings against 4 - because
    # re-centring every label disturbed the many that were already fine to
    # rescue the few that were not. Kept the simple version; the four
    # remaining findings are cosmetic and listed in the README.
    rb = rotated_bbox(raw_boxes[ref], ang)
    ref_label_pos[ref] = (round(rb[1] - 0.5, 3), 0.8)

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
    bx0, by0, bx1, by1 = rotated_bbox(raw_boxes[ref], angles.get(ref, 0.0))
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

# Power trunks that route_board.py widens after routing. Everything routes
# at the Default class's 0.2 mm because that is what fits between the 0.5 mm
# pads of the SODIMM socket and the VQFN parts; the neck-down pass then
# fattens each trunk wherever there is room. The ladder is sized toward the
# 5 A the input protection is built for (IPC-2221, 1 oz external, 10 C rise):
#   1.50mm -> ~4.35A   1.20mm -> ~3.71A   1.00mm -> ~3.25A  0.80mm -> ~2.76A
#   0.60mm -> ~2.24A   0.50mm -> ~1.96A   0.40mm -> ~1.67A  0.30mm -> ~1.33A
# Listing an ambitious top rung costs nothing where it does not fit - the
# pass just falls back a rung.
TRUNK_WIDTH_LADDER = [1.5, 1.2, 1.0, 0.8, 0.6, 0.5, 0.4, 0.3]
TRUNK_NETS = ["VBAT_IN", "VBAT_F", "+12V_PROT", "+5V", "USB_VBUS"]
for _n in TRUNK_NETS:
    assert _n in net_num, f"{_n} is not a net on this board"

board.to_file(PCB)

# `pcb upgrade` fills in any property KiCad's writer left bare using its
# own per-name default - and for Reference that default is visible, at
# (0,0,0), on F.SilkS, i.e. dead centre on the part's own pads. Patch the
# bare tokens to real positions BEFORE upgrade runs, while their exact
# one-line form is still known.
txt = open(PCB, encoding="utf-8").read()
for ref, (dy, fs) in ref_label_pos.items():
    txt = txt.replace(
        f'(property "Reference" "{ref}")',
        f'(property "Reference" "{ref}" (at 0 {dy} 0) (layer "F.SilkS") '
        f'(effects (font (size {fs} {fs}) (thickness 0.12))))')
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
    bx0, by0, bx1, by1 = rotated_bbox(raw_boxes[ref], angles.get(ref, 0.0))
    fx, fy = loaded[ref].position.X, loaded[ref].position.Y
    if (fx + bx0 < board_x0 or fy + by0 < board_y0
            or fx + bx1 > board_x0 + board_w
            or fy + by1 > board_y0 + board_h):
        outside.append(ref)
if outside:
    raise SystemExit(f"parts outside the board outline: {sorted(outside)}")

# No two parts may overlap. DRC does report this, but as hundreds of
# clearance and shorting findings that bury everything else - far better
# to fail here naming the two parts.
placed_box = {}
for ref in parts:
    bx0, by0, bx1, by1 = rotated_bbox(raw_boxes[ref], angles.get(ref, 0.0))
    fx, fy = loaded[ref].position.X, loaded[ref].position.Y
    placed_box[ref] = (fx + bx0, fy + by0, fx + bx1, fy + by1)
clash = []
refs = sorted(placed_box)
for i, a in enumerate(refs):
    ax0, ay0, ax1, ay1 = placed_box[a]
    for b in refs[i + 1:]:
        bx0, by0, bx1, by1 = placed_box[b]
        if ax0 < bx1 - 1e-6 and bx0 < ax1 - 1e-6 and            ay0 < by1 - 1e-6 and by0 < ay1 - 1e-6:
            clash.append((a, b))
if clash:
    raise SystemExit(f"{len(clash)} overlapping part pair(s), first 10: "
                     f"{clash[:10]}")
print(f"  all {len(parts)} parts inside the outline")
