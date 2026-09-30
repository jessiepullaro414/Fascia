#!/usr/bin/env python3
"""
finish_routes.py - route the connections FreeRouting leaves open.

route_board.py gets most of the way and then stalls on a handful of nets
(150 passes left 3; a second run left 1 by its own count and 8 by DRC, so
restarting is a coin flip). This finishes them deterministically: for each
connection kicad-cli DRC reports as open between two pads, it runs an A*
search through the actual copper on the board and lays down real tracks and
vias.

How it works, and why it is shaped this way:

  * The board is rasterised to a 0.05 mm grid, one boolean "blocked" layer per
    copper layer. Every pad, track and via NOT on the net being routed is
    stamped with its own size plus the 0.2 mm clearance; the result is then
    grown by half a track width (for tracks) or by a via's radius (for
    vias), so a free cell really is a legal centre line. Items on the net
    itself are skipped - touching your own copper is the point.
  * Search is 8-direction on each layer with through-vias between them.
    Vias are only allowed where every layer is clear at via size, which is
    what a through via actually needs. A via costs as much as 3 mm of track,
    so it is used to get out of a jam and not as a habit.
  * Fine-pitch pads are reachable because the search starts from the grid
    node nearest the true pad centre, and the first and last segments run
    from the pad centre to that node (at most 0.035 mm). A neighbour 0.5 mm
    away leaves a 0.375 mm corridor on the pad axis, wider than the
    0.3 mm a track needs, so the escape survives the rasterisation.
  * Zones are NOT treated as obstacles. A new track through a GND pour just
    gets a clearance cut when the pour is refilled, so the zones are removed
    before routing and rebuilt afterwards by route_board.add_and_fill_zones()
    (one per process - see that function for why).

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
CLEARANCE = 0.2      # what kicad-cli actually enforces (see route_board.py)
VIA_D, VIA_DRILL = 0.6, 0.3
EDGE_CLEAR = 0.5     # min_copper_edge_clearance
VIA_COST = 3.0       # mm of track a via is worth
MAX_EXPANSIONS = 15_000_000

OPEN_RE = re.compile(r"(?:PTH |SMD )?[Pp]ad ([^ ]+) \[([^\]]+)\] of ([^ ]+)")


# ---------------------------------------------------------------------------
# Driver (system Python): find the open pairs, run the router under KiCad's
# Python, rebuild the zones, verify.
# ---------------------------------------------------------------------------
def open_pairs():
    import route_board as rb
    report = os.path.join(os.environ.get("TEMP", HERE), "finish_routes_drc.json")
    subprocess.run([rb.find_kicad_cli(), "pcb", "drc", "--format", "json",
                    "--output", report, PCB], capture_output=True, text=True)
    drc = json.load(open(report, encoding="utf-8"))
    pairs, skipped = [], []
    for u in drc.get("unconnected_items", []):
        hits = [OPEN_RE.search(i.get("description", "")) for i in u["items"]]
        if len(hits) == 2 and all(hits) and hits[0].group(2) == hits[1].group(2):
            pairs.append({"net": hits[0].group(2),
                          "a": [hits[0].group(3), hits[0].group(1)],
                          "b": [hits[1].group(3), hits[1].group(1)]})
        else:
            skipped.append([i.get("description", "") for i in u["items"]])
    return pairs, skipped


def driver():
    import route_board as rb
    pairs, skipped = open_pairs()
    print(f"{len(pairs)} open pad-to-pad connection(s) to route")
    for p in pairs:
        print(f"  {p['net']}: {p['a'][0]}.{p['a'][1]} -> {p['b'][0]}.{p['b'][1]}")
    for s in skipped:
        print("  SKIPPED (not a pad-to-pad pair):", " | ".join(s))
    if not pairs:
        return 0
    json.dump(pairs, open(PAIRS, "w"))
    r = subprocess.run([rb.KICAD_PYTHON, os.path.abspath(__file__), "--route"],
                       text=True)
    if r.returncode != 0:
        # The router saves whatever it managed and always drops the zones
        # first, so they have to be rebuilt either way.
        print("WARNING: not every connection could be routed - see above")
    rb.add_and_fill_zones()
    left = rb.real_unconnected_count()
    print(f"\nkicad-cli DRC: {left} unconnected item(s) remain")
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

    # Zones come off now and are rebuilt by the driver afterwards.
    for z in list(board.Zones()):
        board.Remove(z)
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
        raw = np.zeros((NL, ny, nx), dtype=bool)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.GetNetCode() == netcode:
                    continue
                b = pad.GetBoundingBox()
                for li in pad_layers(pad):
                    stamp_rect(raw[li], MM(b.GetLeft()) - CLEARANCE,
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
                    stamp_capsule(raw[li], MM(p.x), MM(p.y), MM(p.x), MM(p.y), r)
            else:
                s, e = t.GetStart(), t.GetEnd()
                li = lidx.get(t.GetLayer())
                if li is None:
                    continue
                stamp_capsule(raw[li], MM(s.x), MM(s.y), MM(e.x), MM(e.y),
                              MM(t.GetWidth()) / 2 + CLEARANCE)
        # Keep copper off the board edge.
        edge = int(round(EDGE_CLEAR / RES))
        for li in range(NL):
            raw[li, :edge, :] = raw[li, -edge:, :] = True
            raw[li, :, :edge] = raw[li, :, -edge:] = True
        track = np.stack([dilate(raw[li], int(round(TRACK_W / 2 / RES)))
                          for li in range(NL)])
        via_r = int(round(VIA_D / 2 / RES))
        via_block = np.zeros((ny, nx), dtype=bool)
        for li in range(NL):
            via_block |= dilate(raw[li], via_r)
        return track, via_block

    def find_pad(ref, num):
        for fp in board.GetFootprints():
            if fp.GetReference() == ref:
                for pad in fp.Pads():
                    if str(pad.GetNumber()) == str(num):
                        return pad
        raise SystemExit(f"pad {ref}.{num} not found")

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

    DIRS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
            (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]

    def astar(track, via_block, starts, goals):
        goal_set = set(goals)
        gy, gx = goals[0][1], goals[0][2]

        def h(y, x):
            dy, dx = abs(y - gy), abs(x - gx)
            return RES * (max(dy, dx) + (math.sqrt(2) - 1) * min(dy, dx))

        g, came, heap = {}, {}, []
        for s in starts:
            g[s] = 0.0
            heapq.heappush(heap, (h(s[1], s[2]), 0.0, s))
        seen = 0
        while heap:
            _f, gc, node = heapq.heappop(heap)
            if gc > g.get(node, 1e18) + 1e-12:
                continue
            if node in goal_set:
                path = [node]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return path[::-1]
            seen += 1
            if seen > MAX_EXPANSIONS:
                return None
            li, y, x = node
            for dy, dx, w in DIRS:
                yy, xx = y + dy, x + dx
                if not (0 <= yy < ny and 0 <= xx < nx) or track[li, yy, xx]:
                    continue
                if dy and dx and (track[li, y + dy, x] or track[li, y, x + dx]):
                    continue        # no cutting a blocked corner
                nn = (li, yy, xx)
                ng = gc + w * RES
                if ng < g.get(nn, 1e18):
                    g[nn] = ng
                    came[nn] = node
                    heapq.heappush(heap, (ng + h(yy, xx), ng, nn))
            if not via_block[y, x]:
                for lj in range(NL):
                    if lj != li and not track[lj, y, x]:
                        nn = (lj, y, x)
                        ng = gc + VIA_COST
                        if ng < g.get(nn, 1e18):
                            g[nn] = ng
                            came[nn] = node
                            heapq.heappush(heap, (ng + h(y, x), ng, nn))
        return None

    def emit(path, netcode, pad_a, pad_b):
        """Path of (layer, y, x) nodes -> tracks and vias on the board."""
        pts = [(n[0], *point(n[1], n[2])) for n in path]
        pa, pb = pad_a.GetPosition(), pad_b.GetPosition()
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
            if ri == 0:
                xy.insert(0, (MM(pa.x), MM(pa.y)))
            if ri == len(runs) - 1:
                xy.append((MM(pb.x), MM(pb.y)))
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
        return made, len(vias)

    failed = 0
    for p in json.load(open(PAIRS)):
        pad_a, pad_b = find_pad(*p["a"]), find_pad(*p["b"])
        netcode = pad_a.GetNetCode()
        track, via_block = build_masks(netcode)

        def nodes(pad):
            out = []
            pos = pad.GetPosition()
            cy, cx = cell(MM(pos.x), MM(pos.y))
            for li in pad_layers(pad):
                free = nearest_free(track, li, cy, cx)
                if free:
                    out.append((li, free[0], free[1]))
            return out

        starts, goals = nodes(pad_a), nodes(pad_b)
        if not starts or not goals:
            print(f"{p['net']}: no free cell next to "
                  f"{'start' if not starts else 'end'} pad - cannot route")
            failed += 1
            continue
        path = astar(track, via_block, starts, goals)
        if path is None:
            print(f"{p['net']}: NO PATH FOUND")
            failed += 1
            continue
        segs, nvias = emit(path, netcode, pad_a, pad_b)
        length = sum(math.hypot(b[2] - a[2], b[1] - a[1]) * RES
                     for a, b in zip(path, path[1:]) if a[0] == b[0])
        print(f"{p['net']}: routed, {segs} segments, {nvias} via(s), "
              f"~{length:.1f} mm of track")
    board.Save(PCB)
    print("saved", PCB)
    return 1 if failed else 0


if __name__ == "__main__":
    if "--route" in sys.argv:
        sys.exit(route_under_kicad())
    sys.exit(driver())
