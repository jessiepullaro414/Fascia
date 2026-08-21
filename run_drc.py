#!/usr/bin/env python3
"""
run_drc.py - run KiCad's DRC on Fascia.kicad_pcb and classify the result.

A raw violation count is not useful on a board that is deliberately
unrouted, so this separates the findings that mean something from the two
categories that are expected at this stage:

  unconnected_items      nothing is routed yet; route_board.py is next
  lib_footprint_mismatch a KiCad tooling artifact, not a board defect

The second one is worth explaining rather than muting. `kicad-cli pcb
upgrade` rewrites every footprint into KiCad 10's current format - adding
the mandatory Datasheet/Description properties and per-pad zone_connect
fields - while several of the footprints shipped in KiCad's own libraries
are still in an older format that has KiLib_Generator and none of those
fields. DRC then compares the upgraded board copy against the older
library file literally and reports a difference. The geometry is
identical: same pad count, same positions, same silkscreen. Confirmed by
diffing a mismatching footprint against its library file element by
element.

Anything outside those two categories is a real finding and is printed in
full.
"""
import collections
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "Fascia.kicad_pcb")
REPORT = os.path.join(HERE, "Fascia-drc.json")

EXPECTED = {"unconnected_items", "lib_footprint_mismatch"}


def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    for c in (os.path.join("C:" + os.sep, "Program Files", "KiCad", v, "bin",
                           "kicad-cli.exe") for v in ("10.0", "9.0", "8.0")):
        if os.path.isfile(c):
            return c
    raise SystemExit("kicad-cli not found")


def main():
    cli = find_kicad_cli()
    r = subprocess.run([cli, "pcb", "drc", "--format", "json",
                        "-o", REPORT, PCB], capture_output=True, text=True)
    if not os.path.exists(REPORT):
        raise SystemExit(f"DRC did not produce a report: {r.stderr}")

    with open(REPORT, encoding="utf-8") as f:
        d = json.load(f)

    viol = d.get("violations", [])
    unconn = d.get("unconnected_items", [])
    parity = d.get("schematic_parity", [])

    kinds = collections.Counter(v.get("type") for v in viol)
    real = [v for v in viol if v.get("type") not in EXPECTED]

    print(f"schematic parity : {len(parity):4d}"
          f"   {'OK' if not parity else 'MISMATCH - board disagrees with schematic'}")
    print(f"unconnected items: {len(unconn):4d}   expected until routing")
    for k, n in kinds.most_common():
        tag = "expected (tooling artifact)" if k in EXPECTED else "REAL"
        print(f"{k:>17s}: {n:4d}   {tag}")

    if parity:
        print("\nSchematic parity failures - these always matter:")
        for p in parity[:10]:
            print("  -", p.get("description", "")[:120])

    if real:
        print(f"\n{len(real)} real violation(s):")
        for v in real[:20]:
            print("  -", v.get("description", "")[:120])

    # Parity is the one that can never be tolerated: it means the board
    # and the schematic describe different circuits.
    return 1 if (parity or real) else 0


if __name__ == "__main__":
    sys.exit(main())
