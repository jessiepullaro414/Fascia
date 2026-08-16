"""
Functional banking of the Verdin iMX95 X1 connector for schematic capture.

A 260-pin connector cannot be drawn as one symbol - at 2.54 mm pitch a
single block would be ~330 mm tall, taller than any sheet this project
uses. KiCad's answer is a multi-unit symbol, so this module decides which
pin belongs to which unit.

Banks are derived from the pin NAMES in verdin_pinout.py rather than
hand-listed pin numbers, so that re-running tools/extract_verdin_pinout.py
against a newer datasheet revision cannot silently desynchronise the two
files. The tradeoff is that the rules below must stay exhaustive - which
is exactly what verify() enforces.

Unit letters follow how the board actually uses the connector, not the
datasheet's own ordering:

    A  power and ground
    B  control, sequencing and debug
    C  display - the DSI link to the SN65DSI85-Q1, plus backlight control
    D  communications this board uses - CAN to ecu-pcb, I2C, USB for touch
    E  everything this board does not use, which gets NoConnect items

Bank E is not filler. Per this project's KiCad notes, a deliberately
unused pin needs a real NoConnect item rather than a stub label, or ERC
reports it as a dangling/isolated label - so the unused pins have to be
enumerated just as carefully as the used ones.
"""
import re

from verdin_pinout import X1_PINS

# Ordered most-specific first; the first match wins, so e.g. I2C_2_DSI
# lands in the display bank rather than the generic comms bank.
_RULES = [
    ("A", r"^(GND|VCC|VCC_BACKUP|PWR_1V8_MOCI|PMIC_PGOOD)$"),
    ("B", r"^(CTRL_|JTAG_|TAMPER)"),
    ("C", r"^(DSI_1_|I2C_2_DSI|PWM_3_DSI|GPIO_9_DSI|GPIO_10_DSI)"),
    # USB 3.0 SuperSpeed is explicitly NOT used - the only USB on this
    # board is a USB 2.0 host port for the panel's touch controller, which
    # needs the D_N/D_P pair and its enable/overcurrent lines and nothing
    # else. This rule must precede the USB rule below so the SS pairs land
    # in the unused bank on purpose rather than by falling through.
    # Note the two naming styles: the Verdin-standard names are USB_2_SS*,
    # while USB1_TX1_*/USB1_RX1_* are module-specific-pin functions.
    ("E", r"^(USB_2_SS|USB1_(TX|RX))"),
    ("D", r"^(CAN_[12]_|I2C_1_|USB_)"),
    # Everything else is unused by this board.
    ("E", r".*"),
]

UNIT_NAMES = {
    "A": "Power and ground",
    "B": "Control, sequencing and debug",
    "C": "Display - DSI to bridge, backlight control",
    "D": "Communications - CAN, I2C, USB",
    "E": "Unused on this board (no-connect)",
}


def bank_of(name):
    """Which unit letter a given Verdin signal name belongs to."""
    for letter, pattern in _RULES:
        if re.match(pattern, name):
            return letter
    raise AssertionError(f"no bank rule matched {name!r}")  # unreachable: E is .*


def banks():
    """{unit letter: [(pin, name), ...]} sorted by pin number."""
    out = {letter: [] for letter, _ in _RULES}
    for pin in sorted(X1_PINS):
        out[bank_of(X1_PINS[pin])].append((pin, X1_PINS[pin]))
    return out


def verify():
    """
    Prove the banking is a true partition of the connector.

    Every pin exactly once, nothing invented, nothing dropped. This is the
    check that makes it safe to derive banks from name patterns instead of
    an explicit 260-entry list.
    """
    problems = []
    b = banks()

    assigned = [pin for entries in b.values() for pin, _ in entries]
    if len(assigned) != len(set(assigned)):
        dupes = {p for p in assigned if assigned.count(p) > 1}
        problems.append(f"pins assigned to more than one bank: {sorted(dupes)}")

    missing = set(range(1, 261)) - set(assigned)
    if missing:
        problems.append(f"pins in no bank: {sorted(missing)}")

    extra = set(assigned) - set(range(1, 261))
    if extra:
        problems.append(f"pins outside 1..260: {sorted(extra)}")

    # The display bank is what this whole board is built around, so assert
    # its exact contents rather than trusting the pattern to have caught
    # them. These numbers are from Verdin iMX95 datasheet Table 22.
    expected_c = {23, 25, 29, 31, 35, 37, 41, 43, 47, 49,   # DSI lanes + clock
                  53, 55,                                    # I2C_2_DSI
                  17, 19, 21}                                # bridge EN, PWM, BKL EN
    got_c = {pin for pin, _ in b["C"]}
    if got_c != expected_c:
        problems.append(f"display bank wrong: missing {sorted(expected_c - got_c)}, "
                        f"unexpected {sorted(got_c - expected_c)}")

    # The USB split is deliberate and easy to get wrong, so assert it:
    # the USB 2.0 data pair must be usable, SuperSpeed must not be.
    names_d = {name for _, name in b["D"]}
    names_e = {name for _, name in b["E"]}
    for needed in ("USB_2_D_N", "USB_2_D_P"):
        if needed not in names_d:
            problems.append(f"{needed} should be in the comms bank, is not")
    for excluded in ("USB_2_SSTX_P", "USB_2_SSRX_P", "USB1_TX1_P", "USB1_RX1_P"):
        if excluded not in names_e:
            problems.append(f"{excluded} should be in the unused bank, is not")

    return problems


if __name__ == "__main__":
    import sys

    # Signal names are ASCII, but the family summary below uses a
    # multiplication sign; cp1252 stdout would mangle it.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    problems = verify()
    b = banks()
    for letter in ("A", "B", "C", "D", "E"):
        entries = b[letter]
        print(f"\n=== Unit {letter}: {UNIT_NAMES[letter]} ({len(entries)} pins) ===")
        if letter == "E":
            # Too many to list usefully; summarise by signal family.
            fams = {}
            for _, name in entries:
                fam = re.match(r"^[A-Z0-9]+(?:_[0-9]+)?", name).group(0)
                fams[fam] = fams.get(fam, 0) + 1
            print("   " + ", ".join(f"{k}×{v}" for k, v in sorted(fams.items())))
        else:
            for pin, name in entries:
                print(f"   {pin:3d}  {name}")

    print()
    if problems:
        print("BANKING INVALID:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"banking OK: {sum(len(v) for v in b.values())} pins partitioned across "
          f"{len([k for k, v in b.items() if v])} units")
