#!/usr/bin/env python3
"""
finish_routes.py - route the connections FreeRouting leaves open.

route_board.py gets most of the way and then stalls on a handful of nets
(150 passes left 3; a second run left 1 by its own count and 8 by DRC, so
restarting is a coin flip). This finishes them deterministically: for each
connection kicad-cli DRC reports as open, it runs an A* search through the
actual copper on the board and lays down real tracks and vias. When a pad is
walled in, it rips up the tracks boxing it in, routes the stuck net, and
reroutes whatever it ripped on the next round.

How it works, and why it is shaped this way:

  * The board is rasterised to a 0.05 mm grid, one boolean "blocked" layer
    per copper layer. Every pad, track and via NOT on the net being routed
    is stamped with its own size plus the 0.2 mm clearance; the result is
    then grown by half a track width (for tracks) or by a via's radius (for
    vias), so a free cell really is a legal centre line. Items on the net
    itself are skipped - touching your own copper is the point.
  * Search is 8-direction on each layer with through-vias between them.
    Vias are only allowed where every layer is clear at via size, which is
    what a through via actually needs. A via costs as much as 3 mm of track,
    so it is used to get out of a jam and not as a habit.
  * Fine-pitch pads are reachable because the search starts from the grid
    node nearest the true pad centre, and the first and last segments run
    from the pad centre to that node (at most 0.035 mm).
  * A pad can be sealed in. On this board the three connections FreeRouting
    could not finish were all fine-pitch QFP pads whose neighbours' fan-out
    tracks left a single-cell corridor that ended at a wall; a flood fill
    from U9.28 reached only the pad's own 1.55 mm length. So pads and the
    board edge are HARD keep-outs, and other nets' tracks and vias are SOFT:
    the search may cross them at a price (SOFT_PENALTY per cell), and only
    the copper the chosen path actually crosses is ripped up. The next round
    reads the newly opened connections from DRC and routes those.
  * STATUS: this does not converge on Fascia. Open connections went
    3 -> 17 -> 16 -> 21 over four rounds (an earlier version that ripped
    everything near the pocket went 3 -> 20 -> 20), because re-laying a
    ripped net crosses something else. So the driver compares DRC before
    and after and RESTORES THE ORIGINAL BOARD unless the result is strictly
    better. It is kept because the A* core, the sealed-pad diagnosis and the
    safety net are all sound, and because it may work on a less congested
    board; see PLAN.md for what to do about this one.
  * Zones are NOT treated as obstacles. A new track through a GND pour just
    gets a clearance cut when the pour is refilled, so the zones are removed
    before routing and rebuilt afterwards by route_board.add_and_fill_zones()
    (one per process - see that function for why). That also means DRC has
    to be read after the pours are back, or every GND pad looks open.

The grid is an approximation; the real check is kicad-cli DRC afterwards,
which this script runs and reports. It does not length-match anything.

Usage:  python finish_routes.py
"""
import heapq
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "Fascia.kicad_pcb")
PAIRS = os.path.join(os.environ.get("TEMP", HERE), "fascia_open_pairs.json")

RES = 0.05           # grid pitch, mm
TRACK_W = 0.2        # Default class width
CLEARANCE = 0.22     # kicad-cli enforces 0.2; the extra 0.02 absorbs grid rounding
VIA_D, VIA_DRILL = 0.6, 0.3
EDGE_CLEAR = 0.5     # min_copper_edge_clearance
VIA_COST = 3.0       # mm of track a via is worth
QUICK_CAP = 300_000  # expansions before a search is called "not obviously sealed"
MAX_EXPANSIONS = 15_000_000
SOFT_PENALTY = 0.4   # mm-equivalent charged per cell of crossing another net's track
RIP_MARGIN = 0.03    # mm of extra reach when deciding what a new path has crossed
MAX_ROUNDS = int(os.environ.get("FINISH_ROUNDS", 8))

PAD_RE = re.compile(r"(?:PTH |SMD )?[Pp]ad ([^ ]+) \[([^\]]+)\] of ([^ ]+)")
TRACK_RE = re.compile(r"Track \[([^\]]+)\]")
VIA_RE = re.compile(r"Via \[([^\]]+)\]")


# ---------------------------------------------------------------------------
# Driver (system Python): find the open connections, run the router under
# KiCad's Python, rebuild the zones, read DRC again, repeat.
# ---------------------------------------------------------------------------
def classify(item):
    """One DRC item -> {"kind", "net", ...} or None if it is not routable
    copper (a zone, for instance)."""
    desc = item.get("description", "")
    m = PAD_RE.search(desc)
    if m:
        return {"kind": "pad", "net": m.group(2), "num": m.group(1),
                "ref": m.group(3), "uuid": item.get("uuid")}
    m = TRACK_RE.search(desc)
    if m:
        return {"kind": "track", "net": m.group(1), "uuid": item.get("uuid")}
    m = VIA_RE.search(desc)
    if m:
        return {"kind": "via", "net": m.group(1), "uuid": item.get("uuid")}
    return None


def open_pairs():
    import route_board as rb
    report = os.path.join(os.environ.get("TEMP", HERE), "finish_routes_drc.json")
    subprocess.run([rb.find_kicad_cli(), "pcb", "drc", "--format", "json",
                    "--output", report, PCB], capture_output=True, text=True)
    drc = json.load(open(report, encoding="utf-8"))
    pairs, skipped = [], []
    for u in drc.get("unconnected_items", []):
        items = [classify(i) for i in u["items"]]
        if len(items) == 2 and all(items) and items[0]["net"] == items[1]["net"]:
            pairs.append({"net": items[0]["net"], "a": items[0], "b": items[1]})
        else:
            skipped.append([i.get("description", "") for i in u["items"]])
    return pairs, skipped


EXPECTED_VIOLATIONS = {"unconnected_items", "lib_footprint_mismatch"}


def drc_counts():
    """(unconnected items, real violations) straight from kicad-cli. Real
    means anything outside the two categories run_drc.py treats as expected,
    and it matters here because a router that closes connections by
    crowding copper has not finished anything."""
    import route_board as rb
    report = os.path.join(os.environ.get("TEMP", HERE), "finish_routes_count.json")
    subprocess.run([rb.find_kicad_cli(), "pcb", "drc", "--format", "json",
                    "--output", report, PCB], capture_output=True, text=True)
    drc = json.load(open(report, encoding="utf-8"))
    real = [v for v in drc.get("violations", [])
            if v.get("type") not in EXPECTED_VIOLATIONS]
    return len(drc.get("unconnected_items", [])), len(real)


def label(it):
    return f"{it['ref']}.{it['num']}" if it["kind"] == "pad" else it["kind"]


def driver():
    import route_board as rb
    import shutil
    # Never leave the board worse than it was found. Rip-up can cascade and
    # the grid is only an approximation of the real clearance rules, so the
    # result is judged by DRC at the end and thrown away if it is not better.
    backup = PCB + ".before-finish"
    shutil.copyfile(PCB, backup)
    base_open, base_real = drc_counts()
    print(f"starting point: {base_open} unconnected, {base_real} real violation(s)")
    last = None
    for rnd in range(1, MAX_ROUNDS + 1):
        pairs, skipped = open_pairs()
        print(f"\n=== round {rnd}: {len(pairs)} open connection(s) to route ===")
        for p in pairs:
            print(f"  {p['net']}: {label(p['a'])} -> {label(p['b'])}")
        for s in skipped:
            print("  SKIPPED (not routable copper):", " | ".join(s))
        if not pairs:
            break
        # Round 1 is expected to GROW the count: ripping up the tracks that
        # wall a pad in opens the nets it cut, and those are what rounds 2
        # onward put back. So progress is only judged from round 3 on.
        if rnd >= 3 and len(pairs) >= last:
            print("no progress since the last round - stopping")
            break
        last = len(pairs)
        json.dump(pairs, open(PAIRS, "w"))
        r = subprocess.run([rb.KICAD_PYTHON, os.path.abspath(__file__), "--route"],
                           text=True)
        if r.returncode != 0:
            print("WARNING: not every connection could be routed - see above")
        # The router always drops the zones first, so they go back every round.
        rb.add_and_fill_zones()
    if not pairs:
        # Ripped-up power nets were re-laid at the 0.2 mm routing width, so
        # put the trunk neck-down back. Zones come off first for the same
        # reason the router drops them, and go back on after.
        print("\nre-applying the power-trunk neck-down")
        rb.run_kicad_python("Strip zones", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
keep = []
for z in list(board.Zones()):
    board.Remove(z)
    keep.append(z)
board.Save({PCB!r})
print("zones stripped:", len(keep))
''')
        rb.widen_trunks()
        rb.add_and_fill_zones()
    left, real = drc_counts()
    print(f"\nkicad-cli DRC: {left} unconnected item(s), {real} real violation(s)")
    better = left <= base_open and real <= base_real and (left, real) != (base_open, base_real)
    if not better:
        shutil.copyfile(backup, PCB)
        print(f"NOT BETTER than the starting point ({base_open} unconnected, "
              f"{base_real} real) - restored the original board")
        os.remove(backup)
        return 1
    os.remove(backup)
    rb.report_pair_skew()
    return 0 if left == 0 else 1


# ---------------------------------------------------------------------------
# Router (runs under KiCad's bundled Python: needs pcbnew and numpy)
# ---------------------------------------------------------------------------
def route_under_kicad():
    import numpy as np
    import pcbnew

    MM = pcbnew.ToMM
    IU = pcbnew.FromMM
    LAYERS = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
              pcbnew.In4_Cu, pcbnew.B_Cu]
    NL = len(LAYERS)
    lidx = {l: i for i, l in enumerate(LAYERS)}

    board = pcbnew.LoadBoard(PCB)
    # Measured before the zones are removed: asking for the bounding box
    # after Remove() handed back a bare SwigPyObject with no methods.
    bb = board.GetBoardEdgesBoundingBox()
    ox, oy = MM(bb.GetLeft()), MM(bb.GetTop())
    W, H = MM(bb.GetWidth()), MM(bb.GetHeight())

    # Anything removed from the board is parked here for the life of the
    # process. Letting Python free a removed item corrupted SWIG's view of
    # the board: GetBoardEdgesBoundingBox() and then GetFootprints() started
    # returning bare SwigPyObjects with no methods, twice, in two different
    # places. Keeping the objects alive is the fix; the process is short.
    graveyard = []

    # Zones come off now and are rebuilt by the driver afterwards.
    for z in list(board.Zones()):
        board.Remove(z)
        graveyard.append(z)

    nx, ny = int(math.ceil(W / RES)) + 1, int(math.ceil(H / RES)) + 1
    print(f"grid {nx} x {ny} x {NL} layers at {RES} mm")

    def cell(x, y):
        return int(round((y - oy) / RES)), int(round((x - ox) / RES))

    def point(iy, ix):
        return ox + ix * RES, oy + iy * RES

    def disk_offsets(r_cells):
        return [(dy, dx) for dy in range(-r_cells, r_cells + 1)
                for dx in range(-r_cells, r_cells + 1)
                if dy * dy + dx * dx <= r_cells * r_cells + 0.25]

    def dilate(mask, r_cells):
        out = mask.copy()
        for dy, dx in disk_offsets(r_cells):
            if dy == 0 and dx == 0:
                continue
            ys0, ys1 = max(0, dy), ny + min(0, dy)
            xs0, xs1 = max(0, dx), nx + min(0, dx)
            out[ys0:ys1, xs0:xs1] |= mask[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
        return out

    def stamp_rect(m, x0, y0, x1, y1):
        # Edges round to the NEAREST cell, not outward. Rounding outward
        # added up to a whole cell of extra keep-out on every side, which
        # was enough to seal a 0.5 mm-pitch pad into its own corridor: the
        # first run of this script found no free cell next to any of the
        # three pads it was asked to reach. Nearest rounding is off by at
        # most half a cell (0.025 mm) either way; kicad-cli DRC is the
        # judge of whether that matters.
        iy0 = max(0, int(round((y0 - oy) / RES)))
        iy1 = min(ny - 1, int(round((y1 - oy) / RES)))
        ix0 = max(0, int(round((x0 - ox) / RES)))
        ix1 = min(nx - 1, int(round((x1 - ox) / RES)))
        if iy0 <= iy1 and ix0 <= ix1:
            m[iy0:iy1 + 1, ix0:ix1 + 1] = True

    disks = {}

    def disk_mask(rc):
        if rc not in disks:
            yy, xx = np.mgrid[-rc:rc + 1, -rc:rc + 1]
            disks[rc] = (yy * yy + xx * xx) <= rc * rc + 0.25
        return disks[rc]

    def stamp_capsule(m, x0, y0, x1, y1, r):
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(math.ceil(length / RES)))
        rc = int(round(r / RES))
        d = disk_mask(rc)
        for s in range(steps + 1):
            t = s / steps
            cy, cx = cell(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            ya, yb = max(0, cy - rc), min(ny, cy + rc + 1)
            xa, xb = max(0, cx - rc), min(nx, cx + rc + 1)
            if ya < yb and xa < xb:
                m[ya:yb, xa:xb] |= d[ya - (cy - rc):yb - (cy - rc),
                                     xa - (cx - rc):xb - (cx - rc)]

    def pad_layers(pad):
        return [lidx[l] for l in pad.GetLayerSet().Seq() if l in lidx]

    def build_masks(netcode):
        """Hard and soft keep-outs. Pads and the board edge are HARD: a
        track can never go there. Other nets' tracks and vias are SOFT: the
        search may cross them at a price, and whatever it crosses is ripped
        up afterwards. Splitting them is the point - ripping up everything
        near a stuck pad rebuilt more than it fixed (20 open after three
        rounds), where ripping only what the new path actually crosses
        disturbs the minimum."""
        hard = np.zeros((NL, ny, nx), dtype=bool)
        soft = np.zeros((NL, ny, nx), dtype=bool)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() == netcode:
                    continue
                b = pad.GetBoundingBox()
                for li in pad_layers(pad):
                    stamp_rect(hard[li], MM(b.GetLeft()) - CLEARANCE,
                               MM(b.GetTop()) - CLEARANCE,
                               MM(b.GetRight()) + CLEARANCE,
                               MM(b.GetBottom()) + CLEARANCE)
        for t in board.GetTracks():
            if t.GetNetCode() == netcode:
                continue
            if t.GetClass() == "PCB_VIA":
                p = t.GetPosition()
                r = MM(t.GetWidth(pcbnew.F_Cu)) / 2 + CLEARANCE
                for li in range(NL):
                    stamp_capsule(soft[li], MM(p.x), MM(p.y), MM(p.x), MM(p.y), r)
            else:
                s_, e_ = t.GetStart(), t.GetEnd()
                li = lidx.get(t.GetLayer())
                if li is None:
                    continue
                stamp_capsule(soft[li], MM(s_.x), MM(s_.y), MM(e_.x), MM(e_.y),
                              MM(t.GetWidth()) / 2 + CLEARANCE)
        # Keep copper off the board edge.
        edge = int(round(EDGE_CLEAR / RES))
        for li in range(NL):
            hard[li, :edge, :] = hard[li, -edge:, :] = True
            hard[li, :, :edge] = hard[li, :, -edge:] = True
        half = int(round(TRACK_W / 2 / RES))
        via_r = int(round(VIA_D / 2 / RES))
        track_hard = np.stack([dilate(hard[li], half) for li in range(NL)])
        track_soft = np.stack([dilate(soft[li], half) for li in range(NL)]) & ~track_hard
        via_hard = np.zeros((ny, nx), dtype=bool)
        via_soft = np.zeros((ny, nx), dtype=bool)
        for li in range(NL):
            via_hard |= dilate(hard[li], via_r)
            via_soft |= dilate(soft[li], via_r)
        via_soft &= ~via_hard
        return track_hard, track_soft, via_hard, via_soft

    def nearest_free(track, li, cy, cx, reach=6):
        best = None
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                y, x = cy + dy, cx + dx
                if 0 <= y < ny and 0 <= x < nx and not track[li, y, x]:
                    d = dy * dy + dx * dx
                    if best is None or d < best[0]:
                        best = (d, y, x)
        return best and (best[1], best[2])

    by_uuid = {}

    def index_items():
        by_uuid.clear()
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                by_uuid[pad.m_Uuid.AsString()] = pad
        for t in board.GetTracks():
            by_uuid[t.m_Uuid.AsString()] = t

    def resolve(item, track):
        """A DRC item -> (graph nodes the search may start or end on, the
        point a new track should begin at or None to begin at the node)."""
        obj = by_uuid.get(item.get("uuid"))
        nodes, anchor = [], None
        if item["kind"] == "pad":
            if obj is None:
                for fp in board.GetFootprints():
                    if fp.GetReference() == item["ref"]:
                        for pad in fp.Pads():
                            if str(pad.GetNumber()) == str(item["num"]):
                                obj = pad
            pos = obj.GetPosition()
            anchor = (MM(pos.x), MM(pos.y))
            cy, cx = cell(*anchor)
            for li in pad_layers(obj):
                free = nearest_free(track, li, cy, cx)
                if free:
                    nodes.append((li, free[0], free[1]))
        elif obj is not None and obj.GetClass() == "PCB_VIA":
            pos = obj.GetPosition()
            cy, cx = cell(MM(pos.x), MM(pos.y))
            for li in range(NL):
                free = nearest_free(track, li, cy, cx)
                if free:
                    nodes.append((li, free[0], free[1]))
        elif obj is not None:
            s, e = obj.GetStart(), obj.GetEnd()
            li = lidx[obj.GetLayer()]
            length = math.hypot(MM(e.x) - MM(s.x), MM(e.y) - MM(s.y))
            steps = max(1, int(math.ceil(length / RES)))
            for k in range(steps + 1):
                u = k / steps
                cy, cx = cell(MM(s.x) + (MM(e.x) - MM(s.x)) * u,
                              MM(s.y) + (MM(e.y) - MM(s.y)) * u)
                if 0 <= cy < ny and 0 <= cx < nx and not track[li, cy, cx]:
                    nodes.append((li, cy, cx))
        return nodes, anchor

    DIRS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
            (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]

    def search(masks, starts, goals, cap):
        """A* between node sets. Returns (path, "ok", None), (None, "sealed",
        the cells it could reach) or (None, "capped", None). Crossing a soft
        cell costs SOFT_PENALTY extra per step."""
        track_hard, track_soft, via_hard, via_soft = masks
        goal_set = set(goals)
        gy, gx = goals[0][1], goals[0][2]

        def h(y, x):
            dy, dx = abs(y - gy), abs(x - gx)
            return RES * (max(dy, dx) + (math.sqrt(2) - 1) * min(dy, dx))

        g, came, heap = {}, {}, []
        for s_ in starts:
            g[s_] = 0.0
            heapq.heappush(heap, (h(s_[1], s_[2]), 0.0, s_))
        seen = 0
        while heap:
            _f, gc, node = heapq.heappop(heap)
            if gc > g.get(node, 1e18) + 1e-12:
                continue
            if node in goal_set:
                path = [node]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return path[::-1], "ok", None
            seen += 1
            if seen > cap:
                return None, "capped", None
            li, y, x = node
            for dy, dx, w in DIRS:
                yy, xx = y + dy, x + dx
                if not (0 <= yy < ny and 0 <= xx < nx) or track_hard[li, yy, xx]:
                    continue
                if dy and dx and (track_hard[li, y + dy, x] or track_hard[li, y, x + dx]):
                    continue        # no cutting a blocked corner
                nn = (li, yy, xx)
                ng = gc + w * RES + (SOFT_PENALTY if track_soft[li, yy, xx] else 0.0)
                if ng < g.get(nn, 1e18):
                    g[nn] = ng
                    came[nn] = node
                    heapq.heappush(heap, (ng + h(yy, xx), ng, nn))
            if not via_hard[y, x]:
                for lj in range(NL):
                    if lj != li and not track_hard[lj, y, x]:
                        nn = (lj, y, x)
                        ng = gc + VIA_COST + (SOFT_PENALTY * 12 if (
                            via_soft[y, x] or track_soft[lj, y, x]) else 0.0)
                        if ng < g.get(nn, 1e18):
                            g[nn] = ng
                            came[nn] = node
                            heapq.heappush(heap, (ng + h(y, x), ng, nn))
        return None, "sealed", list(g)

    def rip_crossed(path, netcode):
        """Remove the tracks and vias of other nets that the new path
        actually comes within clearance of - and nothing else."""
        # Per-layer sample points along the path; a via touches every layer.
        pts = {li: [] for li in range(NL)}
        for (li, y, x), nxt in zip(path, path[1:] + [None]):
            px_, py_ = point(y, x)
            pts[li].append((px_, py_))
            if nxt is not None and nxt[0] != li:
                for lj in range(NL):
                    pts[lj].append((px_, py_))
        arr = {li: np.array(v) for li, v in pts.items() if v}
        removed, nets = 0, set()
        for t in list(board.GetTracks()):
            if t.GetNetCode() == netcode:
                continue
            if t.GetClass() == "PCB_VIA":
                q = t.GetPosition()
                ax = bx = MM(q.x)
                ay = by = MM(q.y)
                limit = MM(t.GetWidth(pcbnew.F_Cu)) / 2
                layers = list(arr)
            else:
                li = lidx.get(t.GetLayer())
                if li is None or li not in arr:
                    continue
                s_, e_ = t.GetStart(), t.GetEnd()
                ax, ay, bx, by = MM(s_.x), MM(s_.y), MM(e_.x), MM(e_.y)
                limit = MM(t.GetWidth()) / 2
                layers = [li]
            limit += CLEARANCE + TRACK_W / 2 + RIP_MARGIN
            hit = False
            for li in layers:
                p = arr[li]
                dx, dy = bx - ax, by - ay
                L2 = dx * dx + dy * dy
                if L2 == 0:
                    d = np.hypot(p[:, 0] - ax, p[:, 1] - ay)
                else:
                    u = np.clip(((p[:, 0] - ax) * dx + (p[:, 1] - ay) * dy) / L2, 0, 1)
                    d = np.hypot(p[:, 0] - (ax + u * dx), p[:, 1] - (ay + u * dy))
                if d.min() < limit:
                    hit = True
                    break
            if hit:
                nets.add(t.GetNetname())
                board.Remove(t)
                graveyard.append(t)
                removed += 1
        index_items()
        if removed:
            print(f"  crossed and ripped up {removed} item(s) on: "
                  f"{', '.join(sorted(nets))}")
        return removed

    def emit(path, netcode, anchor_a, anchor_b):
        """Path of (layer, y, x) nodes -> tracks and vias on the board."""
        pts = [(n[0], *point(n[1], n[2])) for n in path]
        runs, cur = [], [pts[0]]
        vias = []
        for prev, nxt in zip(pts, pts[1:]):
            if nxt[0] != prev[0]:
                vias.append((prev[1], prev[2]))
                runs.append(cur)
                cur = [nxt]
            else:
                cur.append(nxt)
        runs.append(cur)
        made = 0
        for ri, run in enumerate(runs):
            xy = [(p[1], p[2]) for p in run]
            if ri == 0 and anchor_a:
                xy.insert(0, anchor_a)
            if ri == len(runs) - 1 and anchor_b:
                xy.append(anchor_b)
            # Drop collinear interior points.
            keep = [xy[0]]
            for i in range(1, len(xy) - 1):
                a, b, c = keep[-1], xy[i], xy[i + 1]
                cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
                if abs(cross) > 1e-9:
                    keep.append(b)
            keep.append(xy[-1])
            for a, b in zip(keep, keep[1:]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6:
                    continue
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pcbnew.VECTOR2I(IU(a[0]), IU(a[1])))
                t.SetEnd(pcbnew.VECTOR2I(IU(b[0]), IU(b[1])))
                t.SetWidth(IU(TRACK_W))
                t.SetLayer(LAYERS[run[0][0]])
                t.SetNetCode(netcode)
                board.Add(t)
                made += 1
        for vx, vy in vias:
            v = pcbnew.PCB_VIA(board)
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetPosition(pcbnew.VECTOR2I(IU(vx), IU(vy)))
            v.SetWidth(IU(VIA_D))
            v.SetDrill(IU(VIA_DRILL))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNetCode(netcode)
            board.Add(v)
        index_items()
        return made, len(vias)

    def route_pair(p):
        """Route one connection through soft obstacles, then rip up exactly
        what it crossed. Returns True on success."""
        net = p["net"]
        netcode = board.GetNetcodeFromNetname(net)
        masks = build_masks(netcode)
        track_hard = masks[0]
        starts, anchor_a = resolve(p["a"], track_hard)
        goals, anchor_b = resolve(p["b"], track_hard)
        if not starts or not goals:
            print(f"{net}: no free cell next to "
                  f"{'start' if not starts else 'end'} - cannot route")
            return False
        path, why, _pocket = search(masks, starts, goals, QUICK_CAP)
        if path is None and why == "capped":
            # Not sealed at the start; check the goal end before paying for a
            # full search that a pad walled in by other PADS would exhaust.
            rpath, rwhy, _rp = search(masks, goals, starts, QUICK_CAP)
            if rpath is not None:
                path = rpath[::-1]
            elif rwhy == "sealed":
                why = "sealed"
            else:
                path, why, _p = search(masks, starts, goals, MAX_EXPANSIONS)
        if path is None:
            reason = "walled in by pads" if why == "sealed" else "search gave up"
            print(f"{net}: NO PATH FOUND ({reason})")
            return False
        rip_crossed(path, netcode)
        segs, nvias = emit(path, netcode, anchor_a, anchor_b)
        length = sum(math.hypot(b[2] - a[2], b[1] - a[1]) * RES
                     for a, b in zip(path, path[1:]) if a[0] == b[0])
        print(f"{net}: routed, {segs} segments, {nvias} via(s), ~{length:.1f} mm of track")
        return True

    index_items()
    failed = 0
    for p in json.load(open(PAIRS)):
        if not route_pair(p):
            failed += 1
    board.Save(PCB)
    print("saved", PCB)
    return 1 if failed else 0


if __name__ == "__main__":
    if "--route" in sys.argv:
        sys.exit(route_under_kicad())
    sys.exit(driver())
