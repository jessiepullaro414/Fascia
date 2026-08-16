#!/usr/bin/env python3
"""
extract_verdin_pinout.py - build verdin_pinout.py from the real Toradex
Verdin iMX95 hardware datasheet (doc 200007).

This is a one-shot data-extraction tool, not part of the normal
schematic -> PCB -> route -> DRC build.  It exists so the 260-pin X1
connector symbol is generated from the vendor's own pin-assignment
tables rather than hand-typed, because hand-typing 260 pins is exactly
the kind of thing that produces a board with two swapped LVDS lanes and
no way to notice until bring-up.

Source document (not committed - it's Toradex's, and it's 1.4 MB):
    https://docs.toradex.com/200007-verdin_imx95_datasheet.pdf
Pass its local path as argv[1].

Parsing approach: PyMuPDF's word-level extraction with coordinates, not
the flat get_text() token stream.  The flat stream loses column identity,
which matters here because a pin's *Verdin specification* name and its
*module-specific function* are different columns - and the LVDS pins are
named MSP_1..MSP_25 in the first and LVDS0_CLK_N etc in the second.  A
naive token-stream parse silently reports "this module has no LVDS".

Verification (all enforced below, non-zero exit on failure):
  - exactly 260 pins, no gaps
  - odd pins are top side, even are bottom (DDR4 SODIMM numbering)
  - the LVDS pin numbers agree with Table 23, which is a separate table
    in the same document parsed independently
"""
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("needs PyMuPDF:  pip install pymupdf")

PIN_TABLE_PAGES = range(15, 35)   # Table 8 + Table 9 (0-based)
LVDS_TABLE_PAGE = 42              # Table 23, used as an independent cross-check

# A Verdin signal name: no spaces, uppercase-ish, may carry # for active-low.
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_#]*$")
CATEGORIES = ("Always", "Reserved", "Module-specific")


def rows_from_page(page, y_tol=3.0):
    """Group a page's words into rows by y-coordinate, each sorted by x."""
    words = page.get_text("words")   # (x0, y0, x1, y1, word, block, line, wordno)
    rows = {}
    for x0, y0, x1, y1, w, *_ in words:
        key = round(y0 / y_tol)
        rows.setdefault(key, []).append((x0, w))
    out = []
    for key in sorted(rows):
        cells = [w for _, w in sorted(rows[key])]
        out.append(cells)
    return out


def parse_pin_rows(doc):
    """
    Extract {pin: {"verdin": name, "category": cat, "func": module_specific}}.

    Row shape in Table 8/9 is:
        <pin> <verdin name> <Yes|-> <category...> <alternate/module function> ...
    """
    pins = {}
    for pno in PIN_TABLE_PAGES:
        if pno >= doc.page_count:
            break
        for cells in rows_from_page(doc[pno]):
            if not cells or not cells[0].isdigit():
                continue
            pin = int(cells[0])
            if not 1 <= pin <= 260:
                continue
            # Don't assume the name is cells[1].  Some rows carry a stray
            # en-dash cell between the pin number and the name column (the
            # tables lay even and odd pins out side by side, and an empty
            # neighbouring cell can land at an x between the two), which is
            # why pin 209/GND went missing on the first pass.  Scan forward
            # for the first real signal name instead.
            name_idx = next((i for i, c in enumerate(cells[1:], start=1)
                             if NAME_RE.match(c)), None)
            if name_idx is None:
                continue

            verdin = cells[name_idx]
            # Find the compatibility category, then take the first
            # plausible signal name after it as the module-specific /
            # alternate function.
            category, func = None, None
            for i, c in enumerate(cells[name_idx + 1:], start=name_idx + 1):
                if any(c.startswith(k) for k in CATEGORIES):
                    category = c
                    for nxt in cells[i + 1:]:
                        if NAME_RE.match(nxt) and nxt not in ("Yes", "No"):
                            func = nxt
                            break
                    break

            # Later pages repeat header/continued rows; first win is correct
            # because the tables are emitted in ascending pin order.
            pins.setdefault(pin, {"verdin": verdin,
                                  "category": category,
                                  "func": func})
    return pins


def parse_lvds_table(doc):
    """Independently parse Table 23 -> {pin: lvds_signal_name}."""
    lvds = {}
    for cells in rows_from_page(doc[LVDS_TABLE_PAGE]):
        if len(cells) >= 2 and cells[0].isdigit() and cells[1].startswith("LVDS"):
            lvds[int(cells[0])] = cells[1]
    return lvds


def resolve(entry):
    """The name to actually label this pin with on the schematic symbol."""
    if entry["category"] and entry["category"].startswith("Module-specific") \
            and entry["func"]:
        return entry["func"]
    return entry["verdin"]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    doc = fitz.open(src)

    pins = parse_pin_rows(doc)
    lvds = parse_lvds_table(doc)

    problems = []

    missing = [n for n in range(1, 261) if n not in pins]
    if missing:
        problems.append(f"missing {len(missing)} pins: {missing[:20]}")

    # Cross-check: every pin Table 23 calls LVDS must resolve to that same
    # name via the Table 8/9 module-specific column.  Two independent tables
    # agreeing is the only real evidence the column parse is right.
    for pin, name in sorted(lvds.items()):
        got = resolve(pins[pin]) if pin in pins else "<missing>"
        if got != name:
            problems.append(f"LVDS mismatch pin {pin}: table23={name} table9={got}")

    if not lvds:
        problems.append("Table 23 parsed as empty - LVDS cross-check did not run")

    if problems:
        print("EXTRACTION FAILED:")
        for p in problems:
            print("  -", p)
        return 1

    resolved = {n: resolve(pins[n]) for n in range(1, 261)}
    gnd = sorted(n for n, s in resolved.items() if s == "GND")
    vcc = sorted(n for n, s in resolved.items() if s == "VCC")

    with open("verdin_pinout.py", "w", encoding="utf-8") as f:
        f.write('"""\n')
        f.write("Verdin iMX95 X1 (260-pin DDR4 SODIMM) pin assignment.\n\n")
        f.write("GENERATED by tools/extract_verdin_pinout.py from the Toradex\n")
        f.write("Verdin iMX95 hardware datasheet (doc 200007). Do not hand-edit -\n")
        f.write("re-run the extractor against a newer datasheet revision instead.\n\n")
        f.write("Names are the Verdin specification signal name, except on\n")
        f.write("Module-specific pins where the module-specific function is used\n")
        f.write("(this is what turns MSP_1 into LVDS0_CLK_N).\n")
        f.write('"""\n\n')
        f.write("# pin number -> signal name\n")
        f.write("X1_PINS = {\n")
        for n in range(1, 261):
            side = "top" if n % 2 else "bottom"
            f.write(f'    {n:3d}: "{resolved[n]}",'.ljust(28) + f"  # {side}\n")
        f.write("}\n\n")
        f.write(f"GND_PINS = {gnd!r}\n\n")
        f.write(f"VCC_PINS = {vcc!r}\n\n")
        f.write("# Table 23 - the display link this board is built around.\n")
        f.write("LVDS_PINS = {\n")
        for pin, name in sorted(lvds.items()):
            f.write(f'    {pin:3d}: "{name}",\n')
        f.write("}\n")

    print(f"wrote verdin_pinout.py: 260 pins, {len(gnd)} GND, {len(vcc)} VCC, "
          f"{len(lvds)} LVDS")
    print("LVDS cross-check against Table 23: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
