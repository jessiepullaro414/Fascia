# Fascia — KiCad schematic + PCB

Companion KiCad project for "Fascia", a 10-inch Android Automotive touch
display head unit: sibling to
[`manifold-pcb`](https://github.com/jessiepullaro414/Manifold),
[`thermo-pcb`](https://github.com/jessiepullaro414/Thermo) and `ecu-pcb`,
same 12V automotive constraints as the rest of the family.

Unlike the rest of the family this is a **two-board** project, because the
panel is physically detached from the main board and sits across the dash
from it:

1. **Carrier board** (behind the dash) — a Toradex Verdin iMX95 System on
   Module on a custom carrier: automotive 12V front end, LVDS display
   output, backlight control, CAN FD to talk to `ecu-pcb`, and the touch
   I2C bus.
2. **Panel board** (in the bezel) — terminates the ribbon at the screen:
   panel LVDS connector, capacitive touch controller, backlight LED
   connector.

## Status

**Schematic started — the module connector is in, the rest is not.**
`build_schematic.py` generates a real, KiCad-loadable schematic
containing the Verdin iMX95 X1 connector. No PCB yet.

`kicad-cli sch erc` reports **50 violations, all `pin_not_connected`** —
and that is the expected, correct result at this stage. They are exactly
the 50 pins in the control, display and communications banks (plus
`VCC_BACKUP`, `PWR_1V8_MOCI` and `PMIC_PGOOD`) that the not-yet-existing
power tree, bridge, transceiver and panel connector will terminate. The
build script asserts that the unconnected set matches that expectation,
so the count going up is a real regression rather than noise.

What is already final:

- all 260 X1 pins exist, banked into 6 units
- 47 GND pins tied to ground, 5 VCC pins tied to +5V, both rails carrying
  PWR_FLAGs since no regulator is on the sheet yet
- 158 unused pins carrying real `NoConnect` items

What exists right now:

- `tools/extract_verdin_pinout.py` — extracts the full 260-pin Verdin
  iMX95 X1 connector pinout from Toradex's own hardware datasheet.
- `verdin_pinout.py` — the generated result: all 260 pins, 47 GND, 5 VCC,
  and the 20 LVDS pins, cross-checked against a second independent table
  in the same document.
- `verdin_x1.py` — functional banking of those 260 pins into multi-unit
  symbol units, with a verifier that proves the banking is a true
  partition of the connector.
- `build_schematic.py` — generates `Fascia.kicad_sch`, `Fascia.kicad_sym`,
  `sym-lib-table` and (on first run only) `Fascia.kicad_pro`.

Three things that were needed to get ERC down from 268 findings to the
50 real ones, recorded because each cost a debug cycle:

1. **Power symbols must touch the net graphically.** A power symbol's own
   pin sits at its local origin with zero length, so placing it *near* a
   connector pin leaves both dangling — 155 `pin_not_connected` findings.
   The fix is a short stub wire from the pin out to the symbol, which
   also keeps the symbol graphic off the pin name.
2. **PWR_FLAGs need the same graphical reachability.** A flag placed in
   isolation, relying on name-based net merging, does not satisfy
   `power_pin_not_driven` (54 findings). Placing each flag on a real
   point of its net clears it.
3. **`kicad-cli` needs a `.kicad_pro` to resolve `${KIPRJMOD}`.** Without
   one it cannot find the project-local `sym-lib-table` and reports one
   `lib_symbol_issues` warning per placed symbol (60 findings), even
   though the symbols are embedded in the schematic and perfectly valid.

Everything below the "Design decisions" section is a plan, not a
description of something that exists. See "Known open items".

### Why X1 is banked into units

A 260-pin connector cannot be one schematic symbol — at 2.54 mm pitch a
single block is ~330 mm tall, taller than any sheet this project uses.
KiCad's answer is a multi-unit symbol, so `verdin_x1.py` assigns each pin
to a unit based on how *this board* uses the connector:

| Unit | Pins | Contents |
| --- | --- | --- |
| A | 55 | Power and ground |
| B | 16 | Control, sequencing, JTAG, tamper |
| C | 15 | Display — the DSI link to the bridge, plus backlight control |
| D | 16 | Communications — CAN to `ecu-pcb`, I2C, USB 2.0 for touch |
| E | 158 | Unused on this board (gets NoConnect items) |

Banks are derived from pin *names* rather than hand-listed pin numbers,
so re-running the extractor against a newer datasheet revision cannot
silently desynchronise the two files. That only works if the rules stay
exhaustive, which `verify()` enforces: every pin exactly once, nothing
invented, nothing dropped, plus explicit assertions on the two groupings
most worth getting right —

- the **display bank** must be exactly the 15 pins from datasheet
  Table 22, asserted by number rather than trusted to a pattern;
- the **USB split** must put the USB 2.0 data pair in the comms bank and
  SuperSpeed in the unused bank. This board's only USB is a 2.0 host port
  for the panel's touch controller, so the SS pairs are unused *on
  purpose*. Worth an explicit rule because they appear under two
  different naming styles — Verdin-standard `USB_2_SS*` and
  module-specific `USB1_TX1_*`/`USB1_RX1_*` — and a single `USB_` pattern
  silently catches one and misses the other.

Bank E is not filler. Per this project's KiCad notes a deliberately
unused pin needs a real `NoConnect` item, not a stub label, or ERC
reports it as a dangling/isolated label — so the unused pins have to be
enumerated as carefully as the used ones.

## Why this SoC, and why not the one I started with

The project began as "10-inch Android Automotive display on an NXP
i.MX 93". **The i.MX 93 cannot run Android**, and this was worth catching
before any board work started:

- The i.MX 93 has no 3D GPU — only the PXP 2D pixel pipeline.
- Since Android 10, NXP has excluded GPU-less i.MX parts from Android
  support outright, because Android's GPU and extension requirements
  can't be met. Running it would mean maintaining a private Android fork
  on software rendering.
- NXP's current i.MX Android BSP covers the 8M Mini/Nano/Plus/Quad, 8ULP,
  8QuadMax/XPlus and i.MX 95. Not the 93.

Replaced with the **i.MX 95**, which has an Arm Mali-G310 (OpenGL ES 3.2,
Vulkan 1.2, 2D+3D), is on NXP's Android BSP list, and is automotive
qualified.

### Why a SoM instead of a raw i.MX 95

A raw i.MX 95 is a 19×19 mm BGA paired with LPDDR5. That forces HDI
construction (laser microvias, blind/buried vias, 12–14 layers just to
escape the BGA) and roughly 90 length- and skew-matched LPDDR5 nets.

That last part is the disqualifier for this project specifically:
FreeRouting cannot do length-matched differential DDR routing, so the DDR
work would have to be manual — which directly contradicts this family's
rule that the generator scripts are the source of truth and the board is
always regenerated, never hand-edited.

Putting the SoC on a **Toradex Verdin iMX95** module moves the LPDDR5 and
its HDI stackup onto a part Toradex has already qualified, and leaves a
carrier board that is connectors, power and differential pairs — squarely
inside what the existing generate → route → verify pipeline handles.

## Design decisions

### Display link: 1920×1200, DSI-to-LVDS bridge on the carrier

The panel is detached and 20 cm–1 m away across the dash, so the link
that crosses that gap must be **LVDS** — differential and low-swing, so
it survives alternator and ignition noise. Options for the long run:

| Option | Verdict |
| --- | --- |
| **LVDS over shielded FFC** | **Chosen for the cable run.** Robust over a dash-length ribbon, and what 10.1" panels natively accept. |
| MIPI DSI direct to the panel | Rejected. D-PHY is designed for on-PCB or very short flex (~15–20 cm); unshielded ribbon across a dash is an EMI problem. |
| HDMI | Not native on the module. Needs a DSI-to-HDMI bridge on the carrier *and* a receiver board behind the panel bridging back. Two extra bridges. |
| FPD-Link III / GMSL2 | The right answer for genuinely long runs (10 m+), overkill and expensive here. |

**How the LVDS gets generated is the part that took real work.**

The module's own display ceilings, from datasheet Table 3: **LVDS 1× up
to 1920×1080**, **MIPI DSI 1× quad lane up to 4K**.

The 10.1" panel market is overwhelmingly **16:10** (1280×800 and
1920×1200), not 16:9. 1920×1200 needs ~154 MHz of pixel clock, which is
**above the module's 1920×1080 native LVDS ceiling** — both Toradex and
NXP's summary cap i.MX 95 LVDS at 1080p60. Forum figures of 170 MHz for
dual-channel split mode are **i.MX 6**, a different generation, and must
not be extrapolated to this part.

So: take **MIPI DSI off the module** (where 1920×1200 is far inside the
4K ceiling) and convert to dual-channel LVDS **on the carrier board**,
right at the ribbon connector, with a **TI SN65DSI85-Q1** — AEC-Q100
qualified, dual-channel LVDS out, 24 bpp, pixel clock 25–154 MHz.

The DSI run stays on-PCB where D-PHY is happy (centimetres); the long,
noisy dash run is still LVDS. Cost is one bridge IC.

**The module's native LVDS pins (88–144) are therefore unused.** They
remain documented in `verdin_pinout.py` in case the design ever falls
back to 1080p native.

**Unexpected benefit:** the whole DSI pin group sits on Verdin-standard
*Reserved* pins (17–55) rather than the *Module-specific* pins native
LVDS uses. This design is therefore **more** portable to other Verdin
modules than the native-LVDS version would have been — it inverts the
portability caveat noted below.

**Confirmed DSI pin group** (Verdin iMX95 datasheet Table 22), which
carries not just the lanes but every control signal the bridge and
backlight need:

| X1 pin | Signal | Role |
| --- | --- | --- |
| 23 / 25 | DSI_1_D3_N / _P | DSI data lane 3 |
| 29 / 31 | DSI_1_D2_N / _P | DSI data lane 2 |
| 35 / 37 | DSI_1_CLK_N / _P | DSI clock |
| 41 / 43 | DSI_1_D1_N / _P | DSI data lane 1 |
| 47 / 49 | DSI_1_D0_N / _P | DSI data lane 0 |
| 53 / 55 | I2C_2_DSI_SDA / SCL | SN65DSI85-Q1 configuration |
| 17 | GPIO_9_DSI | bridge enable |
| 19 | PWM_3_DSI | backlight brightness |
| 21 | GPIO_10_DSI | backlight enable |

### SN65DSI85-Q1: confirmed from the TI datasheet (SLLSEJ4B)

The mode this whole design depends on is **explicitly supported** —
datasheet Table 5, "Single DSI Input to Dual-Link LVDS":

> Single DSI Input on Channel A to Dual-Link LVDS output with Odd pixels
> on Channel A and Even pixels on Channel B.

Register configuration for that mode:

| CSR | Field | Value |
| --- | --- | --- |
| 0x10.6:5 | `DSI_CHANNEL_MODE` | `01` — single-channel DSI receiver |
| 0x10.4:3 | `CHA_DSI_LANES` | `00` — four lanes enabled |
| 0x18.4 | `LVDS_LINK_CFG` | `0` — dual-link LVDS |
| 0x18.3 | `CHA_24BPP_MODE` | `1` — 24 bpp |
| 0x18.1 / 0x18.0 | `CHA/CHB_24BPP_FORMAT1` | selects Format 1 vs Format 2 |

Other confirmed facts that drive the schematic:

- **AEC-Q100 Grade 2, −40 °C to +105 °C ambient.** Better rated than the
  Verdin module itself, and consistent with the rest of this family.
- **Single 1.8 V supply** (VCC 1.65 / 1.8 / 1.95 V). No second rail.
- **HTQFP-64 (PAP)** with thermal pad. RθJA 36.1 °C/W, max case
  temperature 92.2 °C — the pad needs a real thermal via field.
- **REFCLK (pin 17) is optional** — the LVDS pixel clock can be sourced
  from the free-running D-PHY clock instead. If unused it **must be
  pulled to ground through an external resistor**, not left floating.
- **ADDR (pin 64)** selects the I2C target address; if strapped high it
  must go to the *same* 1.8 V rail as VCC, not any other 1.8 V source.
- **IRQ (pin 33)** is an interrupt output back to the SoC.
- **EN** may be driven by `GPIO_9_DSI` or by an RC (≈200 kΩ + ≈200 nF).
  It must only go high *after* VCC is valid. To reset with VCC already
  high, EN must be held low ≥10 ms.
- **Sequencing constraint for the driver:** the DSI CLK lane must be in
  HS and the DSI data lanes driven to LP11 while the device is in reset,
  before EN is asserted.
- For 18 bpp the `Y3` pairs are left NC; at 24 bpp all four data pairs
  per link are used — so the panel connector carries 10 pairs.
- `CHA_SYNC_DELAY` must be programmed to at least 32 pixel clocks.

**Everything is configured over I2C — there is no format strapping.**
That is a meaningful de-risking result: the VESA-versus-JEIDA mapping
question is a *software* choice (`24BPP_FORMAT1`), so picking the panel
later cannot force a board respin over it.

### Timing margins — corrected

An earlier revision of this file claimed 1920×1200 sat "exactly at the
bridge's 154 MHz ceiling". **That was wrong**, and the error mattered:
the 25–154 MHz figure is the **LVDS output clock per link**, and in
dual-link mode each link carries half the pixels. TI's own power-table
reference case is literally 1920×1200 dual-link with
**LVDS CLK OUT = 81.6 MHz**.

The real picture, with every number now confirmed against a primary
source:

| Constraint | Value | Limit | Utilisation |
| --- | --- | --- | --- |
| i.MX 95 DSI transmit, per lane | ~925 Mbps | **2500 Mbps** | **~37% — huge headroom** |
| LVDS output clock, per link | ~81.6 MHz | 154 MHz | ~53% — comfortable |
| **Bridge DSI input, per lane** | ~925 Mbps | **1 Gbps** | **~93% — the bottleneck** |

The i.MX 95 side is confirmed a non-issue: **IMX95AEC Rev 8, Table 58,
`RATE[TX]` Transmit Serial Data Rate = 80 to 2500 Mbps per lane**, and
the feature summary lists "1x 350 MHz MIPI-DSI (4-lane, 2.5 Gbps/lane)
supporting 4kp30 or 3840 x 1440p60".

So the **SN65DSI85-Q1 is the sole bottleneck** in the chain. That is
acceptable — TI documents 1920×1200 as a supported use case for this
part — but it means the bridge, not the SoC, is what to re-examine if
the timing ever fails to close.

The same datasheet confirms the native LVDS ceiling recorded above:
"2x 1080p60 LVDS Tx (2x 4-lane or 1x 8-lane)".

- 1920×1200p60 CVT-RB ≈ 154 MHz pixel clock × 24 bpp = ~3.70 Gbps across
  4 lanes = ~925 Mbps/lane. Under the bridge's 1 Gbps limit, with ~7%
  headroom.
- With **standard** (non-reduced) blanking the pixel clock is ~193 MHz →
  ~1.16 Gbps/lane, which **exceeds** the bridge limit (though not the
  SoC's). Reduced-blanking timing is therefore mandatory, not merely
  preferable.
- Fallback remains 1920×1080, which relaxes the DSI side to
  ~830 Mbps/lane.

### Layout constraints (i.MX 95 Hardware Design Guide UG10210 Rev 2.0)

- **100 Ω differential** for MIPI DSI *and* LVDS, ±10% (Table 40). Also
  50 Ω single-ended for everything else unless specified. This sits
  comfortably inside the SN65DSI85-Q1's own 90–132 Ω LVDS output range.
- For MIPI-DSI compliance tests 1.1.4/1.1.5, keep **parasitic
  capacitance of each DSI trace below 10 pF** — relevant because the DSI
  run crosses the carrier from X1 to the bridge.
- The module's unused native LVDS pins are simply left **not connected**
  (datasheet Table 4). The `VDD_LVDS_1P8`-to-ground-via-10 kΩ guidance in
  that same table applies to the SoC, which is Toradex's side of the
  boundary, not this carrier's.

Note the Verdin carrier must still follow Toradex's own Carrier Board
Design Guide (doc 108140) for anything X1-side; UG10210 describes the
raw SoC.

For reference, the module's native LVDS pins, should the fallback be
needed:

| X1 pin | Channel 0 | X1 pin | Channel 1 |
| --- | --- | --- | --- |
| 88 / 90 | LVDS0_CLK_N / _P | 118 / 120 | LVDS1_CLK_N / _P |
| 94 / 96 | LVDS0_D0_N / _P | 124 / 126 | LVDS1_D0_N / _P |
| 100 / 102 | LVDS0_D1_N / _P | 130 / 132 | LVDS1_D1_N / _P |
| 106 / 108 | LVDS0_D2_N / _P | 136 / 138 | LVDS1_D2_N / _P |
| 112 / 114 | LVDS0_D3_N / _P | 142 / 144 | LVDS1_D3_N / _P |

### Candidate panels evaluated

Real parts found, with the specs that actually drive the board design:

**Selected: iFan IF101GRL192-120B** (2026-08-16), pending the vendor
datasheet and pricing. Chosen mainly on temperature — it is a full 10 °C
better at both ends than the other 1920×1200 candidate, which is the
difference between reliable and marginal on a sun-baked dash — with 89%
DCI-P3 gamut as a real bonus for image quality.

<https://ifan-display.com/product/10-1-inch-touch-panel-1000-nits-wide-color-gamut-1920x1200/>

| Part | Res | Nits | Interface | Touch | Bonded | Temp |
| --- | --- | --- | --- | --- | --- | --- |
| **iFan IF101GRL192-120B** ← selected | 1920×1200 | 1000 | LVDS, 60-pin | **PCAP inc.** | ? | **−30 to +80 °C** |
| Riverdi RVT101HVLNWC00-B | 1280×800 | ~1000 | LVDS | PCAP inc. | **yes** | industrial |
| CDTech S101BWU78EP | 1920×1200 | 1000 | LVDS, 45-pin FPC | no | no | −20 to +70 °C |
| CDTech S101HWX101ED | 1280×800 | 1000 | 4-lane LVDS, 40-pin FPC | no | no | −30 to +80 °C |
| CDTech S101HWX80NP-FC81 | 1280×800 | 350 | 1-ch LVDS, 30-pin | ILI2511 (USB) | no (G+G) | −20 to +70 °C |
| AUO G101UAN02.0 | 1920×1200 | 800 | **MIPI** | no | no | **−10 to +60 °C** |

**The market bifurcates, and neither half is the full target spec:**

- You can get **1920×1200 + 1000 nit + touch** (iFan), but optical
  bonding is unconfirmed and there is no public datasheet — it is a
  contact-the-vendor part.
- Or you can get **productized optical bonding + PCAP + real datasheets
  + distributor stock** (Riverdi), but only at 1280×800.

The full combination is realistically a **semi-custom order with MOQ**,
not an off-the-shelf purchase.

AUO's G101UAN02.0 is ruled out on two counts despite being the obvious
1920×1200 name: it is **MIPI, not LVDS** (which would put D-PHY on the
dash ribbon, the thing this whole design avoids), and −10 to +60 °C is
unusable in a car.

Three things to carry forward:

1. **The backlight boost designs are not interchangeable** — 27–34 V at
   240 mA versus 13.5–17 V at 360 mA versus 5 V at 530 mA are three
   different converters. The panel must be chosen before the backlight
   circuit is designed. iFan publishes only a total figure (9.32 W), so
   the string configuration has to be asked for.
2. **Temperature does not track resolution.** CDTech's 1920×1200 part is
   −20 to +70 °C while their 1280×800 is −30 to +80 °C. A dash bakes in
   sun, so this is a real selection criterion, not a footnote — it is the
   main reason the iFan part leads.
3. **Touch is often USB, not I2C.** Both CDTech modules with touch use
   USB controllers (ILI2511), and iFan lists "USB/I2C". This matters —
   see below.

### Touch interface: USB may beat I2C over this cable run

The original plan assumed I2C touch. The market says otherwise, and on
reflection USB is probably the better engineering choice here anyway:

- **I2C is single-ended** and was designed for on-board use. Over 20 cm
  it is fine; over a metre it is marginal — bus capacitance eats the
  rise time and there is no noise rejection in a car's environment.
- **USB is differential and designed for cables.** A metre is nothing.
  It also arrives as a standard HID device, so Android needs no custom
  touch driver or device-tree work.
- Cost is one differential pair on the ribbon instead of two
  single-ended wires, and a USB host port on the carrier — which the
  Verdin already has.

Decision still open, but it should be made on cable length, not on
which is simpler on paper.

### Power tree and part selection

Every part below was verified against its own datasheet, not chosen from
memory. Two of them corrected an earlier wrong guess, noted inline.

| Role | Part | Key evidence |
| --- | --- | --- |
| Front-end protection | **LM74930-Q1** | AEC-Q100 grade 1 (−40/+125 °C), 4–65 V in, reverse protection to −65 V, adjustable OC/OV, circuit breaker, "meets automotive ISO7637 transient requirements with a suitable TVS diode" |
| 12 V → 5 V buck | **LM61460-Q1** | AEC-Q100 grade 1, 3–36 V in (42 V transient), 6 A, synchronous, low-EMI with spread spectrum |
| CAN FD | **TCAN1044V-Q1** | AEC-Q100 grade 1, VIO 1.7–5.5 V with explicit 1.8 V support, CAN FD to 8 Mbps, ±58 V bus fault protection |
| DSI→LVDS bridge | **SN65DSI85-Q1** | AEC-Q100 grade 2 (−40/+105 °C), single 1.8 V rail, HTQFP-64 |
| Audio codec | **PCM3168A-Q1** | AEC-Q100, 24-bit 6-in/8-out codec, differential outputs, I2S/TDM, 104 dB, 64-HTQFP |
| USB VBUS switch | **TPS2557-Q1** | Automotive, current limit adjustable 500 mA–5 A, FAULT output, dual thermal sensing |
| Wi-Fi / Bluetooth | **on-module** (u-blox MAYA-W260-00B) | Wi-Fi 6, Bluetooth Classic/LE 5.4, two U.FL connectors — comes with the Verdin `WB` variants |

**The module sets two hard constraints** that drove these choices:

- **VCC is 3.135–5.5 V**, so the main rail is 5.0 V.
- **Verdin I/O is 1.8 V logic** (absolute max 2.1 V). This is the
  constraint that eliminates most automotive CAN transceivers.

#### The 5 V rail needs splitting

Rough budget, with the module figure still a placeholder:

| Load | Estimate |
| --- | --- |
| Verdin iMX95 | ~3 A @ 5 V (unknown — see open items) |
| USB-C charging | 3 A @ 5 V |
| Bridge, codec, CAN, misc | ~0.5 A |
| **Total on 5 V** | **~6.5 A** |

That is over the LM61460-Q1's 6 A, so **two supplies** rather than one
bigger one: a main rail for the module and peripherals, and a second
dedicated to USB VBUS. Splitting is the better design anyway — a phone
drawing a fault current should not be able to brown out the SoC and
reboot Android. Using the same part twice also keeps the BOM narrow.

The backlight boost is deliberately *not* on the 5 V rail — it runs from
the protected 12 V directly, since the panel wants ~27–34 V and routing
that through 5 V would waste a conversion and eat the budget.

#### Bluetooth comes free with the module

The Verdin `WB` variants carry a u-blox **MAYA-W260-00B**: Wi-Fi 6,
**Bluetooth Classic / LE 5.4**, IEEE 802.15.4, and **two U.FL antenna
connectors already on the SoM**. That means no RF design, no antenna
matching and no radio certification work on this carrier — just two U.FL
cables out to external antennas.

Bluetooth Classic covers A2DP for streaming and HFP for hands-free calls.
Wi-Fi plus Bluetooth together is also exactly what **wireless CarPlay and
Android Auto** need, so the USB-C port is for wired mode and charging
rather than being the only way in.

Cost to record: the datasheet carries separate pin tables for "modules
without Wi-Fi", so the `WB` variant consumes some UART and ADC pins that
would otherwise reach the carrier. The variant has to be locked before
the connector banks are final.

#### Two corrections worth recording

**The CAN transceiver must be the `V` variant.** An earlier draft of this
plan named the TCAN1043-Q1, which only level-shifts to 3.3 V or 5 V — it
cannot talk to 1.8 V logic at all. Worse, within the correct family the
datasheet's own device comparison table reads:

| Part | Low-voltage I/O support on pin 5 |
| --- | --- |
| TCAN1044-Q1 | No — pin 5 is a No-Connect |
| **TCAN1044V-Q1** | Yes — pin 5 is VIO, 1.8–5.5 V |

The suffix is the entire difference. Order the wrong one and the board
assembles perfectly and the CAN link never works.

**The surge stopper is TI's, not the obvious ADI part.** The LTC4364 is
the better-known automotive surge stopper and does something the
LM74930-Q1 does not — it *regulates through* an overvoltage event rather
than disconnecting. But its datasheet order table lists only C, I and H
temperature grades with **no AEC-Q100 qualification**, which fails this
family's standard (see `ecu-pcb`, where the last unqualified part was
deliberately swapped out). The LM74930-Q1 is explicitly `-Q1`.

**Correction — the LM74930-Q1 does clamp through.** An earlier revision
of this file claimed it protects only by disconnecting the load, and
warned that a load dump would therefore collapse the 5 V rail and force
an Android reboot. That was wrong, and reading the pin table and spec
table settled it:

- pin 16 is **`OVCLAMP`**: "connect this pin to OV pin for overvoltage
  clamp with circuit breaker (timer) functionality";
- `OVCLAMP` has its own rising/falling thresholds (0.59 V / 0.45 V),
  separate from the plain `OV` disconnect thresholds;
- the timer spec includes **`I(TMR_SRC_OVCLAMP)`, "TMR source current
  during overvoltage clamp"** — a dedicated clamp state, not a
  disconnect;
- and `N(A_R_Count)` gives **32 auto-retry cycles**.

So it regulates the pass FET through an overvoltage event for a period
set by the TMR capacitor, then opens the breaker if the event outlasts
it, then auto-retries. That is genuine surge-stopper behaviour, and it
removes the reboot risk previously recorded here.

What remains real is that the clamp is **timed, not indefinite**: the TMR
capacitor has to be sized to ride out a 200 ms load dump, and the pass
FET has to survive dissipating it for that long. FET SOA selection is
therefore a genuine design task — see "Known open items".

For scale, the LTC4364 datasheet gives the industry-standard transient
shapes: a general automotive transient of tr = 10 µs / VPK = 80 V /
τ = 1 ms, and **load dump of tr = 5 ms / VPK = 60 V / τ = 200 ms**. The
200 ms one carries the energy that matters.

### Panel quality matters more than resolution here

Recorded because it should drive the panel budget. In a car, in rough
order of impact on how good the display actually looks:

1. **Brightness** — consumer panels (250–400 nits) wash out in direct
   sun; automotive panels run 800–1500 nits. This is the dominant factor
   and it drives the backlight boost design (string current, thermals).
2. **Optical bonding** (OCA/LOCA) — removes the air gap between LCD and
   cover glass that otherwise destroys daylight contrast; also prevents
   condensation.
3. **24-bit colour, not 18-bit** — 6-bit-per-channel LVDS panels band
   visibly on the dark gradients Android Automotive uses.
4. **IPS** — driver and passenger both view well off-axis.
5. **Anti-glare / AR coating.**

Resolution ranks below all of these. The target spec is a 1080p,
~1000-nit, optically-bonded, 24-bit IPS panel.

**Caveat worth recording:** LVDS on Verdin is a *Module-specific*
function, not an Always-Compatible or Reserved one (datasheet Table 6
classifies it `Total 1 / Always Compatible 0 / Reserved 0 /
Module-specific 1`). The pins are genuinely on the connector, but this
carrier is therefore tied to the Verdin iMX95 specifically and is not
portable to an arbitrary other Verdin module. MIPI DSI, by contrast, sits
on Verdin-standard Reserved pins (17–55) and would be portable — at the
cost of needing a bridge for a dash-length run.

This is also why `tools/extract_verdin_pinout.py` exists rather than a
hand-typed table: the LVDS pins are named `MSP_1`…`MSP_25` in the Verdin
specification column and only resolve to `LVDS0_CLK_N` etc. in the
module-specific function column. A parse that reads the wrong column
concludes the module has no LVDS at all.

### Other confirmed module facts

- Module: 69.6 × 35.0 × 6.0 mm, 260-pin DDR4 SODIMM edge connector.
- Odd pins top side, even pins bottom (DDR4 SODIMM numbering).
- 5 VCC pins (251, 253, 255, 257, 259), 47 GND, plus `VCC_BACKUP` (249).
- CAN FD: `CAN_1_TX`/`CAN_1_RX` on pins 20/22, `CAN_2` on 24/26.
- I2C_1 on pins 12/14; a dedicated display-side `I2C_2_DSI` on 53/55.
- Backlight: `PWM_3_DSI` (pin 19) for brightness, `GPIO_10_DSI` (pin 21)
  for backlight enable via the on-module PCAL6416A I/O expander.
- HDMI is explicitly *not* natively supported on this module.

## Planned scope

A head unit that **displays and sources audio but does not amplify it**.
Scope revised 2026-08-16 from the original display+touch+CAN.

In scope: display, touch, CAN to `ecu-pcb`, Bluetooth, line-level
pre-outs to an external amplifier, a microphone for calls and voice
control, and USB-C for wired CarPlay / Android Auto plus phone charging.

Explicitly **not** in scope: any power amplifier. This board drives no
speakers — it feeds an existing automotive amp over RCA-level pre-outs.
That single decision keeps the highest-current, highest-heat, highest-EMI
subsystem off the board entirely, which matters in a sealed dash
enclosure that already contains a 1000-nit backlight.

Also still out of scope: reversing camera, automotive Ethernet, and any
radio tuner.

Carrier board blocks:

- 12V automotive front end: reverse-polarity protection, load-dump
  clamping (ISO 7637-2 pulse 5), wide-Vin buck to the module rails.
- Verdin X1 260-pin SODIMM socket.
- Module power sequencing: `CTRL_PWR_EN_MOCI`, `CTRL_RESET_MOCI#`,
  `CTRL_SLEEP_MOCI#`, ignition/ACC sense.
- MIPI DSI from X1 into a **SN65DSI85-Q1** bridge — short, impedance-
  controlled, length-matched on-board runs; the bridge sits right at the
  panel connector so the DSI never reaches the ribbon.
- Bridge configured over `I2C_2_DSI`, enabled by `GPIO_9_DSI`.
- **Audio: PCM3168A-Q1 codec** on I2S/TDM from the module. Five of its
  eight DAC channels drive the pre-outs (front L/R, rear L/R, mono sub);
  one ADC channel takes the microphone. Differential outputs, which is
  the right choice for running line level across a car to an amp.
- **Microphone connector** for a remote mic mounted near the A-pillar or
  headliner, with bias. Placement beats mic quality in a car by a wide
  margin, which is why it is off-board.
- **USB-C port** for wired CarPlay / Android Auto: USB 2.0 data to the
  module's `USB_1` port, and VBUS from a **TPS2557-Q1** switch whose EN
  and FAULT map onto `USB_1_EN` and `USB_1_OC#`. CC1/CC2 carry Rp
  resistors advertising 3 A as a downstream-facing port — no PD
  controller and no negotiation firmware.
- Two **U.FL antenna** pigtails from the module's on-board wireless out
  to external Wi-Fi/Bluetooth antennas.
- Dual-channel LVDS out of the bridge to the panel connector, with
  common-mode chokes and ESD protection on each of the 10 pairs.
- Backlight boost driver, PWM-dimmed via `PWM_3_DSI`, enabled by
  `GPIO_10_DSI`, sized for a high-brightness (~1000 nit) panel rather
  than a consumer-grade one.
- CAN FD transceiver to `ecu-pcb`.

Panel board blocks:

- Ribbon connector from the carrier.
- Panel LVDS FFC connector — pin count set by the chosen panel (the
  leading candidate uses 60-pin).
- Capacitive touch controller — must live at the panel, since capacitive
  sensing will not tolerate a metre of cable between controller and
  sensor. Most candidate modules integrate it already. Interface back
  down the ribbon is **USB or I2C, still to be decided** (see "Touch
  interface" above).
- Backlight LED string connector.

## How it's built

Same script-driven, kiutils/kicad-cli-verified workflow as the sibling
projects: the generator scripts are the source of truth — always
regenerate from them, never hand-edit `Fascia.kicad_sch`/`Fascia.kicad_pcb`
directly.

`tools/extract_verdin_pinout.py` is a one-shot data extractor, not part of
the per-build pipeline. It takes the Toradex datasheet's local path as an
argument (the PDF is Toradex's document and is gitignored, not committed):

```bash
python tools/extract_verdin_pinout.py ~/Downloads/200007-verdin_imx95_datasheet.pdf
```

It refuses to write output unless all 260 pins parse and the LVDS pin
numbers it derived from the main pin-assignment tables agree with the
separate LVDS table on page 42.

## Known open items

- No schematic, PCB, footprints or BOM yet.
- **Panel selected but its datasheet is not yet in hand.** The
  **iFan IF101GRL192-120B** is chosen; a request for datasheet and
  pricing went to the vendor on 2026-08-16. Until it arrives, anything
  that depends on the panel's real numbers is blocked:
  - the backlight boost converter (no string V/I published)
  - the panel-side connector footprint and pinout (60-pin, mapping
    unknown)
  - the touch link (USB vs I2C, see above)
  - bezel/mechanical
- **Questions to put to iFan** — none are answerable from their public
  page:
  - Is optical bonding available, and at what MOQ?
  - LVDS single or dual channel? (1920×1200 at 24 bpp requires dual, so
    this is really a confirmation, but the 60-pin connector needs a
    pinout either way.)
  - Backlight string configuration — volts and milliamps, not just the
    published 9.32 W total. The boost converter cannot be designed
    without it.
  - Touch controller IC, and is the interface USB or I2C?
  - Connector part number, and is a mating FFC/cable available?
  - Operating temperature is quoted as −30 to +80 °C; confirm storage
    range and whether an automotive-qualified variant exists.
- **Touch interface undecided** — USB versus I2C, see the section above.
  Leaning USB on cable-length grounds.
- **DSI timing closes on paper but is not yet proven on hardware.** Every
  number in the chain is now confirmed against a primary source and
  1920×1200 fits, but with only ~7% headroom on the bridge's DSI input
  and a hard requirement for reduced-blanking timing. Worth confirming on
  a Verdin EVK with the real panel before committing to fab. Fallback is
  1920×1080.
- **LVDS mapping convention not yet fixed** — VESA vs JEIDA (a.k.a. SPWG)
  differ in bit ordering and are not interchangeable. Determined by the
  chosen panel.
- **Module temperature rating is industrial, not automotive.** The Verdin
  iMX95 IT variants are −40 °C to +85 °C, unlike the AEC-Q100 parts used
  across the rest of this family. Acceptable for a cabin/dash mount but a
  real deviation from the family's engine-bay-rated standard, and worth a
  deliberate decision rather than an accident.
- **Pass-FET SOA and TMR sizing.** The LM74930-Q1's overvoltage clamp is
  timed rather than indefinite, so the TMR capacitor must be sized to
  ride out a 60 V / 200 ms load dump, and the pass MOSFET must survive
  dissipating that event inside its safe operating area. Neither number
  is chosen yet, and SOA is the one most likely to drive the FET choice.
- **Watch the LM74930-Q1 thermal pad.** Its datasheet says the exposed
  pad (RTN) must be left **floating — explicitly "Do Not connect to GND
  plane"**. That is the opposite of the near-universal VQFN convention,
  so both the footprint and the PCB generator need a deliberate exception
  or the part will be destroyed by a routine ground stitch.
- **Verdin power consumption is still unknown.** The module datasheet
  gives no current figure, deferring to the Verdin Family Specification,
  which has not been pulled. The 5 V rail is provisionally sized on the
  LM61460-Q1's 6 A rather than on a real budget.
- **Unit E may need splitting.** At 158 pins it is ~200 mm per side,
  which fits an A3 sheet only marginally. Splitting it into two units of
  ~79 is the likely fix, but the real constraint will not be known until
  the symbol generator exists.
- Verdin iMX95 datasheet is marked *Preliminary — Subject to change*;
  the pinout should be re-extracted against a final revision before fab.
- Carrier must follow the Verdin Carrier Board Design Guide (Toradex doc
  108140), which has not yet been reviewed.
- Toradex publishes free reference carrier schematics with symbols and
  IPC-7351 footprints — not yet pulled in, but likely the right source for
  the X1 socket footprint rather than drawing one.
