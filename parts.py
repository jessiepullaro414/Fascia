"""
Verified pinouts for the carrier's active parts.

Every entry here was read out of the part's own datasheet, not recalled.
Each block cites its source document so a future revision can be
re-checked rather than re-guessed. Pin tuples are:

    (pin_number, pin_name, electrical_type)

using KiCad's electrical-type vocabulary, so build_schematic.py can hand
them straight to the symbol factory.

Notes marked GOTCHA are things that will destroy a board or silently not
work, and that contradict the obvious default.
"""

# ---------------------------------------------------------------------------
# LM74930-Q1 - automotive ideal diode surge stopper with circuit breaker,
# overvoltage protection and fault output.
# Source: TI SNOSDF6, October 2023. VQFN-24 (RGE).
#
# GOTCHA: the exposed thermal pad (RTN) must be left FLOATING. The
# datasheet says explicitly "Leave exposed pad floating. Do Not connect to
# GND plane." That is the opposite of the usual VQFN convention, so the
# footprint and any ground-stitching pass need a deliberate exception.
#
# GOTCHA: OVCLAMP (16) tied to OV (5) selects overvoltage CLAMP with
# circuit-breaker timing, i.e. the part regulates through a surge instead
# of just disconnecting. Tied to GND instead, that behaviour is disabled.
# ---------------------------------------------------------------------------
LM74930_Q1 = [
    (1,  "DGATE",   "output"),      # ideal-diode FET gate
    (2,  "A",       "passive"),     # ideal-diode anode (that FET's source)
    (3,  "SW",      "passive"),     # battery-sense disconnect switch; may float
    (4,  "UVLO",    "input"),       # tie to VS or EN if unused
    (5,  "OV",      "input"),       # tie to GND if unused
    (6,  "EN",      "input"),       # tie to VS for always-on
    (7,  "MODE",    "input"),       # low disables reverse blocking; tie EN/VS
    (8,  "NC1",     "no_connect"),
    (9,  "TMR",     "passive"),     # fault/clamp timer cap; GND disables OCP
    (10, "IMON",    "output"),      # analog current monitor; may float
    (11, "ILIM",    "passive"),     # overcurrent threshold resistor
    (12, "FLT",     "open_collector"),
    (13, "GND",     "power_in"),
    (14, "HGATE",   "output"),      # pass (load switch) FET gate
    (15, "OUT",     "passive"),     # common source rail
    (16, "OVCLAMP", "input"),       # tie to OV to enable clamp; GND disables
    (17, "NC2",     "no_connect"),
    (18, "ISCP",    "input"),       # tie to C for internal 20 mV SCP threshold
    (19, "CS-",     "input"),
    (20, "CS+",     "input"),       # 50R to the sense resistor
    (21, "NC3",     "no_connect"),
    (22, "VS",      "power_in"),    # 100 nF to GND
    (23, "CAP",     "passive"),     # charge pump; 100 nF across CAP and VS
    (24, "C",       "passive"),     # ideal-diode cathode (that FET's drain)
    # Pad 25 is the exposed pad. It MUST be left floating on this part -
    # see the GOTCHA above. It exists here so the schematic can carry a
    # deliberate NoConnect rather than leaving a netless pad by omission.
    (25, "EP",      "passive"),
]

# ---------------------------------------------------------------------------
# TCAN1044V-Q1 - automotive CAN FD transceiver with 1.8 V I/O support.
# Source: TI SLLSF17D, August 2019, revised March 2025. SOIC-8 (D),
# SOT-8 (DDF) and VSON-8 (DRB) share this pinout.
#
# GOTCHA: the "V" suffix is load bearing. Per the datasheet's own device
# comparison table, TCAN1044-Q1 has pin 5 as a No-Connect while
# TCAN1044V-Q1 has it as VIO. The Verdin's I/O is 1.8 V logic, so ordering
# the non-V part yields a board that assembles perfectly and never talks.
#
# GOTCHA: VCC wants 4.5-5.5 V (so the 5 V rail), while VIO wants the 1.8 V
# rail to match the module. They are different supplies, each needing its
# own 100 nF close to the pin.
# ---------------------------------------------------------------------------
TCAN1044V_Q1 = [
    (1, "TXD",  "input"),
    (2, "GND",  "power_in"),
    (3, "VCC",  "power_in"),        # 4.5-5.5 V
    (4, "RXD",  "output"),
    (5, "VIO",  "power_in"),        # 1.7-5.5 V; 1.8 V here
    (6, "CANL", "bidirectional"),
    (7, "CANH", "bidirectional"),
    (8, "STB",  "input"),           # standby, integrated pull-up
]


# ---------------------------------------------------------------------------
# LM61460-Q1 - automotive 3-36 V, 6 A low-EMI synchronous buck.
# Source: TI SNVSB70F, May 2019, revised June 2021. VQFN-HR-14 (RJR).
#
# GOTCHA: BIAS (1) is not a bypass pin. It feeds the internal LDO and
# should be tied to the OUTPUT rail to improve efficiency - but only while
# Vout is at or below 12 V. Above that the datasheet says tie it to
# ground. At our 5 V output it goes to the output.
#
# GOTCHA: three pins must not float AND must not be grounded - FB (4),
# RT (6) and EN/SYNC (7). Grounding RT is a particularly easy mistake
# since most "set with a resistor to ground" pins tolerate it.
#
# GOTCHA: AGND (3) must connect to BOTH PGND1 (9) and PGND2 (11) on the
# PCB, and VIN1/VIN2 and PGND1/PGND2 each need a low-impedance connection
# to their pair. These are one net electrically but a real layout
# constraint, not something the schematic alone captures.
#
# RBOOT (13) sets the SW-node rise time, so it is the EMI knob on this
# part - relevant here given LVDS and a car radio share the enclosure.
# ---------------------------------------------------------------------------
LM61460_Q1 = [
    (1,  "BIAS",     "power_in"),
    (2,  "VCC",      "power_out"),   # internal LDO; 1 uF to AGND, no ext load
    (3,  "AGND",     "power_in"),
    (4,  "FB",       "input"),       # do not float or ground
    (5,  "PGOOD",    "open_collector"),
    (6,  "RT",       "passive"),     # 5.76k-66.5k to GND -> 200k-2200 kHz
    (7,  "EN/SYNC",  "input"),       # do not float; doubles as sync input
    (8,  "VIN1",     "power_in"),
    (9,  "PGND1",    "power_in"),
    (10, "SW",       "output"),
    (11, "PGND2",    "power_in"),
    (12, "VIN2",     "power_in"),
    (13, "RBOOT",    "passive"),     # SW rise time / EMI
    (14, "CBOOT",    "passive"),     # 100 nF to SW
]


# ---------------------------------------------------------------------------
# TLV767-Q1 - automotive 16 V, 1 A linear regulator, adjustable version.
# Source: TI SBVS381A, April 2020, revised December 2020. 8-pin WSON (DRB).
#
# Chosen as an LDO rather than a buck deliberately: the 1.8 V rail feeds
# the DSI-to-LVDS bridge and the CAN transceiver's VIO, the load is small,
# and a linear regulator contributes no switching noise to a board that
# already has LVDS and a car radio sharing an enclosure.
#
# GOTCHA: pin 3 differs between versions - FB on the adjustable part,
# SNS on the fixed part. This design uses the ADJUSTABLE version, so
# pin 3 is FB and drives the output through an external divider. Neither
# may float.
#
# NOTE (contrast with the LM74930-Q1 above): this thermal pad MAY be
# grounded - "connect this pad to ground or leave floating", and a large
# ground plane is preferred for thermals. Do not generalise either part's
# pad rule to the other.
# ---------------------------------------------------------------------------
TLV767_Q1 = [
    (1, "OUT", "power_out"),
    (2, "NC1", "no_connect"),   # not internally connected; may tie to GND
    (3, "FB",  "input"),        # adjustable version; do not float
    (4, "GND", "power_in"),
    (5, "EN",  "input"),        # internal pull-up; may float to enable
    (6, "GND2", "power_in"),
    (7, "NC2", "no_connect"),
    (8, "IN",  "power_in"),
    (9, "EP",  "passive"),      # exposed pad; grounded on this part
]


# ---------------------------------------------------------------------------
# SN65DSI85-Q1 - automotive dual-channel MIPI DSI to dual-link LVDS bridge.
# Source: TI SLLSEJ4B, July 2016, revised June 2018. HTQFP-64 (PAP).
#
# This board uses datasheet Table 5 mode "Single DSI Input to Dual-Link
# LVDS": DSI channel A only, four lanes, out to both LVDS links with odd
# pixels on A and even on B.
#
# GOTCHA: VCORE (31) is an OUTPUT, not a supply input - it is the 1.1 V
# internal regulator rail and needs a 1 uF capacitor to ground. Feeding
# it would destroy the part.
#
# GOTCHA: RSVD1 (34) and RSVD2 (1) "must be left unconnected for normal
# operation". Not grounded, not pulled - unconnected.
#
# GOTCHA: the unused DSI channel B inputs must ALSO be left unconnected.
# The datasheet states it twice: in the pin table and again under
# CHA_DSI_LANES ("Unused DSI input pins ... must be left unconnected").
#
# GOTCHA: REFCLK (17) is optional - the pixel clock can come from the
# free-running D-PHY clock - but if unused it must be pulled to ground
# through a resistor, not left floating.
#
# GOTCHA: ADDR (64), when strapped high, must tie to the SAME 1.8 V rail
# that feeds VCC, not to any other 1.8 V source.
#
# NOTE: PowerPAD is reference ground here, so it DOES get grounded -
# unlike the LM74930-Q1 pad above. Check each part; do not generalise.
# ---------------------------------------------------------------------------
SN65DSI85_Q1 = [
    (1,  "RSVD2",  "no_connect"),
    (2,  "EN",     "input"),
    (3,  "VCC1",   "power_in"),
    (4,  "DB0P",   "input"),  (5,  "DB0N",  "input"),
    (6,  "DB1P",   "input"),  (7,  "DB1N",  "input"),
    (8,  "DBCP",   "input"),  (9,  "DBCN",  "input"),
    (10, "DB2P",   "input"),  (11, "DB2N",  "input"),
    (12, "DB3P",   "input"),  (13, "DB3N",  "input"),
    (14, "VCC2",   "power_in"),
    (15, "SCL",    "input"),
    (16, "SDA",    "bidirectional"),
    (17, "REFCLK", "input"),
    (18, "VCC3",   "power_in"),
    (19, "DA0P",   "input"),  (20, "DA0N",  "input"),
    (21, "DA1P",   "input"),  (22, "DA1N",  "input"),
    (23, "GND1",   "power_in"),
    (24, "DACP",   "input"),  (25, "DACN",  "input"),
    (26, "GND2",   "power_in"),
    (27, "DA2P",   "input"),  (28, "DA2N",  "input"),
    (29, "DA3P",   "input"),  (30, "DA3N",  "input"),
    (31, "VCORE",  "power_out"),   # 1.1 V regulator OUTPUT; 1 uF to GND
    (32, "VCC4",   "power_in"),
    (33, "IRQ",    "output"),
    (34, "RSVD1",  "no_connect"),
    (35, "VCC5",   "power_in"),
    (36, "A_Y3P",  "output"), (37, "A_Y3N", "output"),
    (38, "A_CLKP", "output"), (39, "A_CLKN", "output"),
    (40, "VCC6",   "power_in"),
    (41, "A_Y2P",  "output"), (42, "A_Y2N", "output"),
    (43, "VCC7",   "power_in"),
    (44, "A_Y1P",  "output"), (45, "A_Y1N", "output"),
    (46, "A_Y0P",  "output"), (47, "A_Y0N", "output"),
    (48, "VCC8",   "power_in"),
    (49, "VCC9",   "power_in"),
    (50, "B_Y3P",  "output"), (51, "B_Y3N", "output"),
    (52, "GND3",   "power_in"),
    (53, "B_CLKP", "output"), (54, "B_CLKN", "output"),
    (55, "VCC10",  "power_in"),
    (56, "B_Y2P",  "output"), (57, "B_Y2N", "output"),
    (58, "VCC11",  "power_in"),
    (59, "B_Y1P",  "output"), (60, "B_Y1N", "output"),
    (61, "B_Y0P",  "output"), (62, "B_Y0N", "output"),
    (63, "VCC12",  "power_in"),
    (64, "ADDR",   "input"),
    (65, "EP",     "passive"),      # PowerPAD is reference ground here
]


# ---------------------------------------------------------------------------
# PCM3168A-Q1 - automotive 24-bit 6-in / 8-out audio codec.
# Source: TI SBAS452A, September 2008, revised January 2016. 64-pin HTQFP
# with PowerPAD (PAP).
#
# GOTCHA, and the important one: this part has TWO supply domains -
# analog VCC at 4.5-5.5 V and digital VDD at 3.0-3.6 V. VDD cannot be run
# at 1.8 V, so it cannot be wired directly to the Verdin. Its VIH minimum
# is 2 V, above what a 1.8 V output can guarantee, and its VOH minimum is
# 2.4 V, ABOVE the module's 2.1 V absolute maximum on 1.8 V I/O. The
# codec-to-module direction would therefore not merely fail, it would
# damage the module. Every digital line between them is level shifted.
#
# GOTCHA: MODE (48) selects the control port. Tied to DGND it is I2C,
# which is what this design uses. Tied to VDD it would be SPI.
#
# NOTE: PowerPAD is connected to ANALOG ground on this part.
# ---------------------------------------------------------------------------
PCM3168A_Q1 = [
    ( 1, "VCOMAD", "passive"),
    ( 2, "AGNDAD2", "power_in"),
    ( 3, "VCCAD2", "power_in"),
    ( 4, "RST", "input"),
    ( 5, "OVF", "output"),
    ( 6, "LRCKAD", "bidirectional"),
    ( 7, "BCKAD", "bidirectional"),
    ( 8, "DOUT1", "output"),
    ( 9, "DOUT2", "output"),
    (10, "DOUT3", "output"),
    (11, "DGND2", "power_in"),
    (12, "VDD2", "power_in"),
    (13, "ZERO", "output"),
    (14, "VCCDA1", "power_in"),
    (15, "VCOMDA", "passive"),
    (16, "AGNDDA1", "power_in"),
    (17, "VOUT8P", "output"),
    (18, "VOUT8N", "output"),
    (19, "VOUT7P", "output"),
    (20, "VOUT7N", "output"),
    (21, "VOUT6P", "output"),
    (22, "VOUT6N", "output"),
    (23, "VOUT5P", "output"),
    (24, "VOUT5N", "output"),
    (25, "VOUT4P", "output"),
    (26, "VOUT4N", "output"),
    (27, "VOUT3P", "output"),
    (28, "VOUT3N", "output"),
    (29, "VOUT2P", "output"),
    (30, "VOUT2N", "output"),
    (31, "VOUT1P", "output"),
    (32, "VOUT1N", "output"),
    (33, "AGNDDA2", "power_in"),
    (34, "VCCDA2", "power_in"),
    (35, "LRCKDA", "bidirectional"),
    (36, "BCKDA", "bidirectional"),
    (37, "DIN1", "input"),
    (38, "DIN2", "input"),
    (39, "DIN3", "input"),
    (40, "DIN4", "input"),
    (41, "SCKI", "input"),
    (42, "SCL", "input"),
    (43, "SDA", "bidirectional"),
    (44, "ADR1", "bidirectional"),
    (45, "ADR0", "input"),
    (46, "VDD1", "power_in"),
    (47, "DGND1", "power_in"),
    (48, "MODE", "input"),
    (49, "VCCAD1", "power_in"),
    (50, "AGNDAD1", "power_in"),
    (51, "VIN1N", "input"),
    (52, "VIN1P", "input"),
    (53, "VIN2N", "input"),
    (54, "VIN2P", "input"),
    (55, "VIN3N", "input"),
    (56, "VIN3P", "input"),
    (57, "VIN4N", "input"),
    (58, "VIN4P", "input"),
    (59, "VREFAD1", "passive"),
    (60, "VREFAD2", "passive"),
    (61, "VIN5N", "input"),
    (62, "VIN5P", "input"),
    (63, "VIN6N", "input"),
    (64, "VIN6P", "input"),
    (65, "EP", "passive"),          # PowerPAD, connected to ANALOG ground
]


# ---------------------------------------------------------------------------
# SN74AXC4T245-Q1 - automotive 4-bit dual-supply level translator.
# Source: TI SCES905F, July 2019, revised January 2024. 16-pin TSSOP (PW).
#
# Two independent direction controls (1DIR, 2DIR) split it into two 2-bit
# banks, so one device can translate in both directions at once. Both
# rails are independently 0.65-3.6 V, and it reaches 380 Mbps translating
# 1.8 V to 3.3 V - comfortably past what I2S needs here.
#
# GOTCHA: OE is active LOW for enable ("pull OE high to place outputs in
# tri-state"), and DIR/OE are referenced to VCCA, not VCCB.
# ---------------------------------------------------------------------------
SN74AXC4T245_Q1 = [
    (1,  "VCCA", "power_in"),
    (2,  "1DIR", "input"),
    (3,  "2DIR", "input"),
    (4,  "1A1",  "bidirectional"),
    (5,  "1A2",  "bidirectional"),
    (6,  "2A1",  "bidirectional"),
    (7,  "2A2",  "bidirectional"),
    (8,  "GND1", "power_in"),
    (9,  "GND2", "power_in"),
    (10, "2B2",  "bidirectional"),
    (11, "2B1",  "bidirectional"),
    (12, "1B2",  "bidirectional"),
    (13, "1B1",  "bidirectional"),
    (14, "2OE",  "input"),
    (15, "1OE",  "input"),
    (16, "VCCB", "power_in"),
]


# ---------------------------------------------------------------------------
# TPS2557-Q1 - automotive current-limited power-distribution switch.
# Source: TI SLVSC97B, March 2014, revised September 2020. 8-terminal
# S-PVSON with thermal pad (DRB).
#
# GOTCHA: EN polarity is the ONLY difference between two otherwise
# identical parts. TPS2557-Q1 is enable-active-HIGH; TPS2556-Q1 is
# active-low. Ordering the wrong one gives a port that is powered exactly
# when it should not be.
#
# Convenient: VIH on EN is 1.1 V, so the module's 1.8 V GPIO drives it
# directly - no level shifter on this path, unlike the audio codec.
#
# FAULT is active-low open drain and needs a pull-up. It is pulled to
# 1.8 V rather than 5 V so it lands inside the module's input rating.
#
# NOTE: thermal pad is internally connected to GND and must also be
# connected externally.
# ---------------------------------------------------------------------------
TPS2557_Q1 = [
    (1, "GND",   "power_in"),
    (2, "IN1",   "power_in"),
    (3, "IN2",   "power_in"),
    (4, "EN",    "input"),          # active HIGH on this variant
    (5, "ILIM",  "passive"),        # 20k - 187k sets the limit
    # OUT1/OUT2 (and IN1/IN2) are the SAME internal node, brought out on
    # two pins to share current. Only one is typed as a driver; typing
    # both power_out makes ERC report a power-output conflict against
    # what is physically one pin.
    (6, "OUT1",  "power_out"),
    (7, "OUT2",  "passive"),
    (8, "FAULT", "open_collector"),
    (9, "EP",    "passive"),        # internally GND, must also connect
]


def verify():
    """Sanity-check the tables before anything builds symbols from them."""
    problems = []
    for name, pins in (("LM74930_Q1", LM74930_Q1),
                       ("TCAN1044V_Q1", TCAN1044V_Q1),
                       ("LM61460_Q1", LM61460_Q1),
                       ("TLV767_Q1", TLV767_Q1),
                       ("SN65DSI85_Q1", SN65DSI85_Q1),
                       ("PCM3168A_Q1", PCM3168A_Q1),
                       ("SN74AXC4T245_Q1", SN74AXC4T245_Q1),
                       ("TPS2557_Q1", TPS2557_Q1)):
        numbers = [p[0] for p in pins]
        if numbers != list(range(1, len(pins) + 1)):
            problems.append(f"{name}: pin numbers are not 1..{len(pins)} "
                            f"contiguous: {numbers}")
        if len(set(p[1] for p in pins)) != len(pins):
            problems.append(f"{name}: duplicate pin names")

    # Both parts are known-size packages; assert the counts so a careless
    # edit that drops a pin is caught here rather than in the netlist.
    if len(LM74930_Q1) != 25:
        problems.append(f"LM74930_Q1 should have 25 pins (24 + exposed pad), has {len(LM74930_Q1)}")
    if len(TCAN1044V_Q1) != 8:
        problems.append(f"TCAN1044V_Q1 should have 8 pins, "
                        f"has {len(TCAN1044V_Q1)}")
    if len(LM61460_Q1) != 14:
        problems.append(f"LM61460_Q1 should have 14 pins, "
                        f"has {len(LM61460_Q1)}")
    if len(TLV767_Q1) != 9:
        problems.append(f"TLV767_Q1 should have 9 pins (8 + EP), has {len(TLV767_Q1)}")
    if len(SN65DSI85_Q1) != 65:
        problems.append(f"SN65DSI85_Q1 should have 65 pins (64 + EP), "
                        f"has {len(SN65DSI85_Q1)}")
    # The bridge has twelve separate VCC pins and they must all reach the
    # 1.8 V rail; a dropped one is a brownout nobody sees on the drawing.
    ncc = sum(1 for _, n, _ in SN65DSI85_Q1 if n.startswith("VCC"))
    if ncc != 12:
        problems.append(f"SN65DSI85_Q1 should have 12 VCC pins, has {ncc}")
    if len(PCM3168A_Q1) != 65:
        problems.append(f"PCM3168A_Q1 should have 65 pins (64 + EP), "
                        f"has {len(PCM3168A_Q1)}")
    if len(TPS2557_Q1) != 9:
        problems.append(f"TPS2557_Q1 should have 9 pins (8 + EP), has {len(TPS2557_Q1)}")
    if len(SN74AXC4T245_Q1) != 16:
        problems.append(f"SN74AXC4T245_Q1 should have 16 pins, "
                        f"has {len(SN74AXC4T245_Q1)}")
    # Eight differential DAC outputs and six differential ADC inputs.
    for pre, want, what in (("VOUT", 16, "DAC output"),
                            ("VIN", 12, "ADC input")):
        got = sum(1 for _, n, _ in PCM3168A_Q1 if n.startswith(pre))
        if got != want:
            problems.append(f"PCM3168A_Q1 should have {want} {what} pins, "
                            f"has {got}")
    return problems


if __name__ == "__main__":
    import sys

    problems = verify()
    for name, pins in (("LM74930-Q1 (VQFN-24)", LM74930_Q1),
                       ("TCAN1044V-Q1 (SOIC-8)", TCAN1044V_Q1),
                       ("LM61460-Q1 (VQFN-HR-14)", LM61460_Q1),
                       ("TLV767-Q1 (WSON-8)", TLV767_Q1),
                       ("SN65DSI85-Q1 (HTQFP-64)", SN65DSI85_Q1),
                       ("PCM3168A-Q1 (HTQFP-64)", PCM3168A_Q1),
                       ("SN74AXC4T245-Q1 (TSSOP-16)", SN74AXC4T245_Q1),
                       ("TPS2557-Q1 (SON-8)", TPS2557_Q1)):
        print(f"\n=== {name}: {len(pins)} pins ===")
        for num, pname, etype in pins:
            print(f"   {num:2d}  {pname:<8s} {etype}")
    print()
    if problems:
        print("PART TABLES INVALID:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("part tables OK")


# ---------------------------------------------------------------------------
# Footprint assignments.
#
# All but one come from KiCad's own libraries, and build_schematic.py
# verifies at build time that every file named here actually exists -
# a typo in a footprint name is otherwise invisible until the netlist is
# imported into the PCB editor.
#
# Exposed pads are pad N+1 in every one of these footprints (65 on the
# TQFP-64s, 25 on the VQFN-24, 9 on the SON-8s), which is why the pin
# tables above carry an explicit EP pin.
# ---------------------------------------------------------------------------
FOOTPRINTS = {
    # The Verdin module plugs into a standard DDR4 SODIMM socket, and
    # KiCad's footprint has exactly 260 pads numbered 1-260 - a direct
    # match for the extracted pinout.
    "Verdin_iMX95_X1": "Connector_PCBEdge:SODIMM-260_DDR4_H4.0-5.2_OrientationStd_Socket",
    "LM74930-Q1":      "Package_DFN_QFN:Texas_RGE0024H_VQFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm",
    "TLV767-Q1":       "Package_DFN_QFN:Texas_DRB0008A",
    "TPS2557-Q1":      "Package_DFN_QFN:Texas_DRB0008A",
    "TCAN1044V-Q1":    "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "SN65DSI85-Q1":    "Package_QFP:Texas_TQFP-64-1EP_10x10mm_P0.5mm_EP8x8mm_Mask4.44x4.44mm",
    "PCM3168A-Q1":     "Package_QFP:Texas_TQFP-64-1EP_10x10mm_P0.5mm_EP8x8mm_Mask4.44x4.44mm",
    "SN74AXC4T245-Q1": "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
    # Generated rather than from a KiCad library: the RJR (VQFN-HR-14)
    # package has no stock footprint. See tools/build_lm61460_footprint.py.
    "LM61460-Q1":      "TI_RJR0014A_VQFN-HR:TI_RJR0014A_VQFN-HR-14_4x3.5mm",
    "R":    "Resistor_SMD:R_0603_1608Metric",
    "C":    "Capacitor_SMD:C_0603_1608Metric",
    "L":    "Inductor_SMD:L_1210_3225Metric",
    "TVS":  "Diode_SMD:D_SMB",
    "FUSE": "Fuse:Fuse_Bourns_MF-RG300",
    "NFET": "Package_TO_SOT_SMD:SOT-23",

    # Connectors. These are PROVISIONAL - 2.54 mm pin headers standing in
    # so the board can be placed and routed end to end. Every one of them
    # is a real connector decision still to be made, and two are blocked:
    # CONN_PANEL waits on the iFan datasheet (the real part is a 60-pin
    # FFC, not 22 pins), and CONN_USBC needs a 24-pad receptacle footprint
    # with a matching 24-pin symbol rather than a 9-pin stand-in.
    "CONN3":      "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
    "CONN_CAN":   "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
    "CONN_PANEL": "Connector_PinHeader_2.54mm:PinHeader_1x22_P2.54mm_Vertical",
    "CONN_PREOUT": "Connector_PinHeader_2.54mm:PinHeader_1x11_P2.54mm_Vertical",
    "CONN_MIC":   "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
    "CONN_USBC":  "Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical",
    "CONN_JTAG":  "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
    "CONN_BTN":   "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "CONN_CELL":  "Battery:BatteryHolder_Keystone_1058_1x2032",
}
