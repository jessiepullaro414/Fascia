#!/usr/bin/env python3
"""
placement_metric.py - total half-perimeter wirelength of an unrouted board.

Placement quality without routing it: for each net, the half-perimeter of
the bounding box of its pads, summed. GND is excluded (it is a plane, so
its pad spread says nothing about routing effort), and so is any net with
more than 12 pads (a rail, not a signal). Also prints the longest nets,
which is where a passive sitting far from its IC shows up.

Runs under KiCad's bundled Python (it needs pcbnew):
  "C:/Program Files/KiCad/10.0/bin/python.exe" tools/placement_metric.py [board]
"""
import os
import sys
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "Fascia.kicad_pcb")
board = pcbnew.LoadBoard(os.path.abspath(path))

pads = {}
for fp in board.GetFootprints():
    for pad in fp.Pads():
        n = pad.GetNetname()
        if n and n != "GND":
            p = pad.GetPosition()
            pads.setdefault(n, []).append((pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)))

rows = []
for n, pts in pads.items():
    if len(pts) < 2 or len(pts) > 12:
        continue
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    rows.append(((max(xs) - min(xs)) + (max(ys) - min(ys)), n, len(pts)))
rows.sort(reverse=True)

bb = board.GetBoardEdgesBoundingBox()
print(f"board {pcbnew.ToMM(bb.GetWidth()):.1f} x {pcbnew.ToMM(bb.GetHeight()):.1f} mm")
print(f"total HPWL {sum(r[0] for r in rows):.0f} mm over {len(rows)} nets")
print("longest:", ", ".join(f"{n} {h:.0f}" for h, n, _ in rows[:10]))
