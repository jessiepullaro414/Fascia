# Fascia plan

Where the board stands, what is left, and what each remaining step is
blocked on. Last updated 2026-09-30.

Legend: [x] done and verified, [~] done but not fully verified or not
finished, [ ] not started, [!] blocked on something outside this repo.

## 1. Where we are

| Stage | State |
| --- | --- |
| Schematic | [~] Generated, ERC has 10 findings; 6 of them wait on the iFan datasheet |
| Footprints | [~] LM61460-Q1 generated from datasheet vector art; connectors are still provisional 2.54 mm headers |
| Placement | [x] 118 parts, 148 x 71 mm, schematic parity 0 |
| Routing | [~] 3 of ~450 connections open (see section 2) |
| Differential pairs | [ ] Routed but not tuned; worst skew is 20 mm |
| BOM | [ ] None yet |
| Fab outputs | [ ] None yet |
| Project webpage | [~] Exists, but still says "schematic only, no PCB yet" |

## 2. Routing: finish the last 3 connections

The board is routed except for `AOUT_RL_N` (U9.28 to J5.6), `U9_ADR1_TIE`
(R36.1 to U9.44) and `LVDS_A_Y0N` (J4.2 to U5.47). FreeRouting cannot
close them: 150 passes oscillated between 3 and 4 unrouted, and a second
run reported 1 unrouted while `kicad-cli` DRC found 8. A stochastic restart
is a coin flip, so `finish_routes.py` routes them deterministically with an
A* search through the real copper and then lets DRC judge.

- [x] `route_board.py` ported from the siblings; streams FreeRouting output
      to `Fascia-route.log` so a slow run is visible
- [x] Placement fixed so routing is tractable: bridge and socket placed
      together, passives pulled next to their IC, spacing 1.3 to 2.0 mm
      (22 unrouted at 12 passes became 4)
- [~] `finish_routes.py` written and working, but it does not close the gap
      yet. Findings:
  - All three open pads are **sealed in**: fine-pitch QFP pads whose
    neighbours' fan-out tracks leave a one-cell corridor that ends at a
    wall (`U9.28` can reach only its own 1.55 mm length). That is why
    FreeRouting stalled on exactly these.
  - With rip-up enabled the script does route all three (41, 31 and 43 mm
    of track), but ripping up what walled them in opens 17 other
    connections, and the next round re-routing those rips up more:
    20 open after round 1, 20 open after round 3, plus 25-45 real
    clearance violations from the 0.05 mm grid. Net result is worse than
    the 3-open board, so the script **restores the original board when DRC
    says it is not better**. Checked: it does.
  - Next idea if this is pursued: negotiated (soft-obstacle) routing, where
    the search may cross other nets' tracks at a cost and only the copper
    the path actually crosses is ripped, instead of everything within
    0.75 mm of the pocket. Pads stay hard obstacles.
- [ ] DRC-verified result: 0 unconnected items, no new clearance violations

Acceptance: `python run_drc.py` reports schematic parity 0, 0 unconnected
items, and nothing outside the two expected categories. The fallback is
hand-routing the three in KiCad, which is now the recommended path: the
three pads are sealed in by neighbouring fan-out, and moving a few of
those tracks by hand is quicker than teaching the script to do it.

## 3. Signal integrity before this is a real board

FreeRouting does not length-match, and the DSI lanes run at about 925 Mbps,
so this is a real gap and not a cosmetic one. `route_board.py` prints the
intra-pair skew; the committed board reads:

| Pair | Skew |
| --- | --- |
| `LVDS_A_Y1` | 20 mm |
| `DSI_D2` | 15 mm |
| `LVDS_B_Y0` | 10 mm |
| `DSI_D3` | 9 mm |
| `LVDS_B_CLK` | 8 mm |

- [ ] Decide the skew budget for DSI (per lane and lane-to-clock) and LVDS
      from the SN65DSI85-Q1 datasheet, not from habit
- [ ] Meander the short leg of each over-budget pair, or re-route the pair
      together
- [ ] Set up a controlled-impedance stackup: 90 to 100 ohm differential on
      DSI and LVDS, and confirm the fab can hit it
- [ ] Re-run the skew report and keep the numbers in the README

## 4. Blocked on the iFan panel datasheet [!]

Requested 2026-08-16, not received. Until it arrives:

- [!] Backlight boost converter: string voltage and current are unpublished,
      so the converter cannot be sized
- [!] Panel connector: the real part is a 60-pin FFC with an unknown
      pinout; the board carries a provisional 22-pin header
- [!] Touch link: USB or I2C depends on the controller IC
- [!] Bezel and mechanical
- [!] LVDS bit-mapping convention (VESA vs JEIDA)

Action: follow up with iFan. The list of questions is in README, "Known
open items". Six of the ten ERC findings clear when this lands.

## 5. Decisions that are still open

- [ ] USB-C receptacle: needs a real 24-pad footprint and a matching
      24-pin symbol; currently a placeholder header
- [ ] `DSI_IRQ` wants a spare 1.8 V GPIO; `IGN_SENSE` wants a module ADC
- [ ] TVS sizing, pass-FET SOA, and TMR capacitor for a 60 V / 200 ms load
      dump (the LM74930-Q1 clamp is timed, not indefinite)
- [ ] Verdin current draw is unknown; the 5 V rail is sized on the
      LM61460-Q1's 6 A, not on a real budget
- [ ] Verdin iMX95 IT variants are industrial (-40 to +85 C), not AEC-Q100;
      acceptable for a cabin mount but it should be a recorded decision
- [ ] Confirm DSI timing on a Verdin EVK with the real panel before fab.
      It closes on paper with about 7% headroom and needs reduced-blanking
      timing; the fallback is 1920 x 1080
- [ ] LM74930-Q1 exposed pad must stay floating, which is the opposite of
      the usual stitch-to-GND rule; confirm the footprint and generator
      both honour it before any fab output

## 6. Path to fab

1. [~] Finish routing (section 2)
2. [ ] Tune the differential pairs (section 3)
3. [!] Resolve the panel-dependent items (section 4)
4. [ ] Replace provisional connectors with real parts and footprints
5. [ ] Clear the remaining silkscreen-over-mask finding
6. [ ] Generate the BOM, following `cluster-pcb/display/build_bom.py`
7. [ ] Purchasing pass: MPNs, stock, lead time (the Verdin SoM and the
       panel first)
8. [ ] Generate fab outputs and run a fab-side DFM check
9. [ ] Second review of the whole schematic against the datasheets

## 7. Software (separate from the board)

- [x] First Android Automotive build succeeded 2026-08-18; Verdin is a
      supported target in `automotive-16.0.0_1.3.0`
- [ ] The M33 System Manager is still built with the EVK board config and
      needs the Verdin one
- [ ] Bring-up on a real carrier: DSI bridge configuration, codec, USB-C
      host mode, CAN to `ecu-pcb`

## 8. Housekeeping

- [x] README "Known open items" no longer claims there is no PCB
- [ ] Project webpage status text (`car-parts-shop`, `projects/fascia`)
      still says schematic only
- [ ] Update the memory note once routing closes
