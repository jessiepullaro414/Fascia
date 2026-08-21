#!/usr/bin/env python3
"""
build_lm61460_footprint.py - generate the LM61460-Q1 land pattern.

TI's RJR0014A (VQFN-HR-14) has no KiCad library footprint, and it is not
a package a generic QFN generator can produce: the pads are all different
sizes, unevenly placed, and the four corner pads are L-SHAPED.

Geometry is not eyeballed off the drawing. The LAND PATTERN EXAMPLE in
the datasheet (TI SNVSB70F, page 55) is real vector art, so the pad
rectangles were extracted from the PDF's own path data with PyMuPDF and
only the scale was inferred - from a dimension the drawing states
outright, pad 10 being 2.4 x 0.4 mm. That scale then reproduced pad 10's
0.400 mm height exactly, which is the check that the whole extraction is
right.

The four L-shaped pads are each built from TWO rectangular pads sharing
one pad number. That is a real, valid KiCad construction - this project's
own notes record a Keystone fuse holder with two physical pads both
numbered "1" - and it is far safer than approximating an L with its
bounding box, which would add copper in the notch and eat the clearance
to the neighbouring pads.

Every L pad's notch faces the OUTER corner of the pattern, confirmed by
rendering the drawing region for pads 1, 9 and 11 rather than assuming
the symmetry held.

Pin names come from parts.py so this file and the schematic cannot
disagree about what pin 10 is.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import parts  # noqa: E402

NAME = "TI_RJR0014A_VQFN-HR-14_4x3.5mm"
LIB = "TI_RJR0014A_VQFN-HR"
OUT_DIR = os.path.join(os.path.dirname(HERE), "footprints",
                       LIB + ".pretty")

# (pin, cx, cy, w, h) in mm, origin at the package centre, Y down.
# L-shaped pads appear twice under the same pin number.
PADS = [
    # pin 1 (BIAS) - L, notch at top-left
    (1, -1.700, -1.675, 0.400, 0.550),
    (1, -1.850, -1.175, 0.700, 0.450),
    (2, -1.850, -0.525, 0.700, 0.250),
    (3, -1.850, 0.000, 0.700, 0.300),
    (4, -1.850, 0.525, 0.700, 0.250),
    # pin 5 (PGOOD) - L, notch at bottom-left
    (5, -1.850, 1.175, 0.700, 0.450),
    (5, -1.700, 1.675, 0.400, 0.550),
    (6, -1.125, 1.600, 0.250, 0.700),
    (7, -0.625, 1.600, 0.250, 0.700),
    (8, 0.450, 1.450, 0.400, 1.000),
    # pin 9 (PGND1) - L, notch at bottom-right
    (9, 1.800, 1.175, 0.800, 0.450),
    (9, 1.600, 1.675, 0.400, 0.550),
    # pin 10 (SW) - the long bar the scale was derived from
    (10, 1.000, 0.000, 2.400, 0.400),
    # pin 11 (PGND2) - L, notch at top-right
    (11, 1.600, -1.675, 0.400, 0.550),
    (11, 1.800, -1.175, 0.800, 0.450),
    (12, 0.450, -1.450, 0.400, 1.000),
    (13, -0.625, -1.600, 0.250, 0.700),
    (14, -1.125, -1.600, 0.250, 0.700),
]

BODY_W, BODY_H = 4.0, 3.5      # nominal package body, datasheet page 54
CY_MARGIN = 0.25               # courtyard beyond the pad extent


def verify():
    """Check the table against what the datasheet independently states."""
    problems = []
    pins = sorted({p[0] for p in PADS})
    if pins != list(range(1, 15)):
        problems.append(f"pins are not 1..14: {pins}")

    # The four corner pads must each be two rectangles; everything else one.
    counts = {}
    for pin, *_ in PADS:
        counts[pin] = counts.get(pin, 0) + 1
    for pin in (1, 5, 9, 11):
        if counts.get(pin) != 2:
            problems.append(f"pin {pin} is L-shaped and needs 2 rects, "
                            f"has {counts.get(pin)}")
    for pin, n in counts.items():
        if pin not in (1, 5, 9, 11) and n != 1:
            problems.append(f"pin {pin} should be one rect, has {n}")

    # Pad 10 is the dimension the whole extraction was scaled from.
    p10 = [p for p in PADS if p[0] == 10][0]
    if (round(p10[3], 3), round(p10[4], 3)) != (2.4, 0.4):
        problems.append(f"pad 10 should be 2.4 x 0.4, is {p10[3]} x {p10[4]}")

    # Overall pad extent, against the package body it has to sit under.
    x0 = min(c - w / 2 for _, c, _, w, _ in PADS)
    x1 = max(c + w / 2 for _, c, _, w, _ in PADS)
    y0 = min(c - h / 2 for _, _, c, _, h in PADS)
    y1 = max(c + h / 2 for _, _, c, _, h in PADS)
    if abs((x1 - x0) - 4.4) > 0.01 or abs((y1 - y0) - 3.9) > 0.01:
        problems.append(f"pad extent {x1 - x0:.2f} x {y1 - y0:.2f}, "
                        f"expected 4.40 x 3.90")

    # No two DIFFERENT pins may overlap. Same-pin rects are meant to.
    for i, (pa, xa, ya, wa, ha) in enumerate(PADS):
        for pb, xb, yb, wb, hb in PADS[i + 1:]:
            if pa == pb:
                continue
            if (abs(xa - xb) < (wa + wb) / 2 - 1e-9
                    and abs(ya - yb) < (ha + hb) / 2 - 1e-9):
                problems.append(f"pads {pa} and {pb} overlap")
    return problems, (x0, y0, x1, y1)


def emit(extent):
    x0, y0, x1, y1 = extent
    names = {n: nm for n, nm, _ in parts.LM61460_Q1}
    cx0, cy0 = x0 - CY_MARGIN, y0 - CY_MARGIN
    cx1, cy1 = x1 + CY_MARGIN, y1 + CY_MARGIN
    bw, bh = BODY_W / 2, BODY_H / 2

    L = [f'(footprint "{NAME}"',
         "	(version 20260206)",
         '	(generator "build_lm61460_footprint.py")',
         '	(generator_version "10.0")',
         '  (layer "F.Cu")',
         '  (descr "Texas Instruments RJR0014A, VQFN-HR-14, 4x3.5mm body, '
         '1mm max height. Land pattern extracted from the vector art in '
         'TI SNVSB70F page 55. Corner pads 1/5/9/11 are L-shaped and are '
         'each two rectangles sharing one pad number.")',
         '  (tags "VQFN-HR RJR0014A LM61460 TI")',
         '  (attr smd)',
         f'  (property "Reference" "U" (at 0 {cy0 - 0.8:.3f} 0) '
         '(layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))',
         f'  (property "Value" "{NAME}" (at 0 {cy1 + 0.8:.3f} 0) '
         '(layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))']

    # Fab body outline with a pin-1 chamfer.
    ch = 0.5
    L.append(f'  (fp_poly (pts (xy {-bw + ch:.3f} {-bh:.3f}) '
             f'(xy {bw:.3f} {-bh:.3f}) (xy {bw:.3f} {bh:.3f}) '
             f'(xy {-bw:.3f} {bh:.3f}) (xy {-bw:.3f} {-bh + ch:.3f})) '
             '(stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))')

    # Courtyard.
    L.append(f'  (fp_rect (start {cx0:.3f} {cy0:.3f}) (end {cx1:.3f} {cy1:.3f}) '
             '(stroke (width 0.05) (type solid)) (fill none) '
             '(layer "F.CrtYd"))')

    # Silkscreen: short marks clear of the pads, plus a pin-1 dot.
    L.append(f'  (fp_line (start {cx0:.3f} {cy0:.3f}) (end {cx0 + 0.8:.3f} '
             f'{cy0:.3f}) (stroke (width 0.12) (type solid)) '
             '(layer "F.SilkS"))')
    L.append(f'  (fp_line (start {cx0:.3f} {cy0:.3f}) (end {cx0:.3f} '
             f'{cy0 + 0.8:.3f}) (stroke (width 0.12) (type solid)) '
             '(layer "F.SilkS"))')
    L.append(f'  (fp_circle (center {cx0 - 0.25:.3f} {cy0 - 0.25:.3f}) '
             f'(end {cx0 - 0.05:.3f} {cy0 - 0.25:.3f}) '
             '(stroke (width 0.12) (type solid)) (fill solid) '
             '(layer "F.SilkS"))')

    for pin, x, y, w, h in PADS:
        L.append(f'  (pad "{pin}" smd rect (at {x:.3f} {y:.3f}) '
                 f'(size {w:.3f} {h:.3f}) '
                 '(layers "F.Cu" "F.Paste" "F.Mask"))')
        # NB: no trailing comment here. KiCad's s-expression format has
        # no comment syntax at all - a "; SW" on the end of a pad line
        # makes the whole library fail to load, silently, with nothing
        # more useful than "Unable to load library".

    L.append(")")
    return "\n".join(L) + "\n"


KICAD_PYTHON = os.path.join("C:" + os.sep, "Program Files", "KiCad", "10.0",
                            "bin", "python.exe")


def verify_with_kicad(path):
    """
    Load the footprint back through KiCad's own engine.

    Self-consistency checks prove the table is coherent; they cannot prove
    KiCad will accept the file. It did not, the first time: pad lines
    carried trailing "; PINNAME" comments, and the s-expression format has
    no comment syntax, so the entire library failed to load with nothing
    more useful than "Unable to load library". Only a real load catches
    that class of error.

    Runs under KiCad's bundled interpreter, which is the only one with
    pcbnew - a different Python from the one running this script.
    """
    if not os.path.exists(KICAD_PYTHON):
        print("  (KiCad python not found; skipping load-back check)")
        return []
    script = (
        "import pcbnew, sys\n"
        "fp = pcbnew.FootprintLoad(%r, %r)\n"
        "if fp is None:\n"
        "    print('LOAD_FAILED'); sys.exit(1)\n"
        "pads = list(fp.Pads())\n"
        "xs, ys = [], []\n"
        "for q in pads:\n"
        "    c, z = q.GetPosition(), q.GetSize()\n"
        "    x, y = pcbnew.ToMM(c.x), pcbnew.ToMM(c.y)\n"
        "    w, h = pcbnew.ToMM(z.x), pcbnew.ToMM(z.y)\n"
        "    xs += [x - w / 2, x + w / 2]; ys += [y - h / 2, y + h / 2]\n"
        "print('PADS', len(pads))\n"
        "print('NUMS', sorted({q.GetNumber() for q in pads}, key=int))\n"
        "print('EXTENT %%.3f %%.3f' %% (max(xs) - min(xs), max(ys) - min(ys)))\n"
    ) % (os.path.dirname(path),
         os.path.splitext(os.path.basename(path))[0])
    r = subprocess.run([KICAD_PYTHON, "-c", script],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    if r.returncode != 0 or "LOAD_FAILED" in out:
        return [f"KiCad could not load the footprint: {out} {r.stderr.strip()}"]

    problems = []
    info = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
    if info.get("PADS") != str(len(PADS)):
        problems.append(f"KiCad sees {info.get('PADS')} pads, wrote {len(PADS)}")
    nums = info.get("NUMS", "")
    if any(f"'{n}'" not in nums for n in range(1, 15)):
        problems.append(f"KiCad pad numbers wrong: {nums}")
    w, h = (float(v) for v in info["EXTENT"].split())
    if abs(w - 4.4) > 0.01 or abs(h - 3.9) > 0.01:
        problems.append(f"KiCad extent {w:.2f} x {h:.2f}, expected 4.40 x 3.90")
    if not problems:
        print(f"  KiCad load-back: {info['PADS']} pads, "
              f"extent {w:.2f} x {h:.2f} mm  OK")
    return problems


def main():
    problems, extent = verify()
    if problems:
        print("FOOTPRINT TABLE INVALID:")
        for p in problems:
            print("  -", p)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, NAME + ".kicad_mod")
    with open(path, "w", encoding="utf-8") as f:
        f.write(emit(extent))

    x0, y0, x1, y1 = extent
    print(f"wrote {path}")
    print(f"  14 pins, {len(PADS)} pads "
          f"({len(PADS) - 14} extra for the four L-shaped corner pads)")
    print(f"  pad extent {x1 - x0:.2f} x {y1 - y0:.2f} mm, "
          f"body {BODY_W} x {BODY_H} mm")

    kp = verify_with_kicad(path)
    if kp:
        print("KICAD REJECTED THE FOOTPRINT:")
        for m in kp:
            print("  -", m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
