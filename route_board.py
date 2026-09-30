"""
Routes Fascia.kicad_pcb with FreeRouting, widens the power trunks, pours
GND on the two inner planes, and then checks the result against KiCad's own
DRC rather than trusting the router's self-reported count.

PORTED from cluster-pcb/display/route_board.py (2026-09-30), which itself
descends from thermo-pcb's. Same pipeline, retargeted at this board's files,
nets and 6-layer stack. Everything below that looks over-cautious was paid
for on a sibling board - read the comments before "simplifying" it.

Steps:
  1. Export a Specctra .dsn with KiCad's OWN pcbnew.ExportSpecctraDSN, run
     under KiCad's bundled Python (the system Python has no pcbnew).
  2. Run FreeRouting headless on it, retrying: it is stochastic and the
     0.5 mm-pitch Verdin SODIMM socket is at the edge of one-shot solvable.
  3. Import the .ses back with pcbnew.ImportSpecctraSES and save.
  4. Neck-down: widen the power trunks wherever there is room.
  5. Pour GND on In1.Cu and In4.Cu AFTER routing (additive copper on an
     already-connected board - pouring first made siblings harder to route
     and hid pads that the fill did not actually reach).
  6. Ask kicad-cli DRC how many connections are really unconnected. This is
     the ground truth: cluster-pcb once had FreeRouting say "0 unrouted"
     while DRC found 5 open nets around a fine-pitch VQFN.

Layer plan (F, In1, In2, In3, In4, B): In1 and In4 are GND, one beside each
outer layer, which gives the DSI and LVDS pairs a reference plane. In2 and
In3 stay free for routing - the GND pours only fill what the traces leave.

NOT handled here, and worth saying plainly: FreeRouting does not
length-match. The DSI and dual-link LVDS pairs are routed as ordinary nets
with no impedance control and no skew tuning. report_pair_skew() at the end
measures what came out so that is a number, not a guess, but tuning is still
open work before this board is real.

Requires tools/freerouting-2.2.4.jar (gitignored, ~57 MB - copy it from a
sibling repo's tools/) and KiCad's bundled Python at KICAD_PYTHON.

Only run on a FRESH unrouted board: re-running on a board whose zones are
already filled reliably hangs FreeRouting. This script regenerates from
build_pcb.py itself if it finds copper already on the board.

Afterwards: python run_drc.py
"""
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "Fascia.kicad_pcb")
DSN = os.path.join(HERE, "Fascia.dsn")
SES = os.path.join(HERE, "Fascia.ses")
FREEROUTING_JAR = os.path.join(HERE, "tools", "freerouting-2.2.4.jar")

KICAD_PYTHON = r"C:\Program Files\KiCad\10.0\bin\python.exe"
JAVA_CANDIDATES = [
    r"C:\Program Files\Eclipse Adoptium\jre-25.0.3.9-hotspot\bin\java.exe",
]

# Autorouter effort: cap passes so a bad/congested board fails fast instead of
# spinning forever, rather than trying to tune "good enough" up front.
# `-oit 0` disables FreeRouting's "stop early if the score hasn't improved
# much in the last 10 passes" behavior, so it keeps trying up to MAX_PASSES
# instead of settling for a plateau. Same value thermo-pcb and cluster-pcb settled on after real 0.5mm-pitch VQFN/LQFP congestion
# needed several stochastic restarts to clear - this board has comparable
# fine-pitch parts (the Verdin X1 SODIMM-260 socket at 0.5mm pitch, the
# Hirose DF40C panel connector at 0.4mm pitch - finer than any sibling
# board's own connectors - plus several 0.5mm-pitch SO-8/VQFN ICs), and
# this board's own flat, max-density individual-component placement (see
# build_pcb.py's own header) packs things noticeably tighter than any
# sibling board's zone-based layout, so real congestion here is at least
# as likely as anywhere else in this family.
# IMPORTANT: only run this on a FRESH unrouted board (via build_pcb.py) -
# thermo-pcb confirmed re-running route_board.py on a board that already has
# zones filled reliably HANGS FreeRouting indefinitely. If a re-route is
# ever needed, regenerate from build_pcb.py first.
MAX_PASSES = 150
OPTIMIZATION_IMPROVEMENT_THRESHOLD = 0


def find_java():
    import shutil
    exe = shutil.which("java")
    if exe:
        return exe
    for candidate in JAVA_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise SystemExit("java not found - see README for the Java + FreeRouting setup")


KICAD_CLI_CANDIDATES = [r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"]


def find_kicad_cli():
    import shutil
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    for candidate in KICAD_CLI_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise SystemExit("kicad-cli not found")


def real_unconnected_count():
    """The real ground truth for "is this board actually fully routed" -
    kicad-cli's own DRC engine, not FreeRouting's own self-reported
    unrouted count. Real, not theoretical: a first real run on this
    board reported "0 unrouted" from FreeRouting/route_until_clean()
    while kicad-cli's own DRC afterward found 5 real unconnected items,
    all clustered around one fine-pitch VQFN footprint - the
    SES import round-trip (or FreeRouting's own final pass) silently
    left real connections incomplete despite the self-reported count
    saying otherwise. This is the check that actually catches that."""
    kicad_cli = find_kicad_cli()
    report_path = os.path.join(os.environ.get("TEMP", HERE), "route_verify_drc.json")
    subprocess.run([kicad_cli, "pcb", "drc", "--format", "json",
                    "--output", report_path, "--exit-code-violations", PCB],
                   capture_output=True, text=True)
    drc = json.load(open(report_path, encoding="utf-8"))
    return len(drc.get("unconnected_items", []))


def run_kicad_python(label, script):
    result = subprocess.run([KICAD_PYTHON, "-c", script], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"{label} failed (exit {result.returncode})")


def export_dsn():
    if not os.path.isfile(KICAD_PYTHON):
        raise SystemExit(f"KiCad's bundled Python not found at {KICAD_PYTHON}")
    # repr() on every embedded path, not an f-string splice - Windows paths
    # contain sequences like "\Users" that a plain (non-raw) generated string
    # literal misreads as a unicode escape (\U...); repr() escapes correctly
    # no matter where the path lands in the generated script text.
    run_kicad_python("DSN export", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ExportSpecctraDSN(board, {DSN!r})
print("DSN export:", "OK" if ok else "FAILED", "->", {DSN!r})
if not ok:
    raise SystemExit(1)
''')


def run_freerouting(quiet=False):
    """Returns the number of connections FreeRouting reported as still
    unrouted (parsed from its own summary line), or None if that line
    couldn't be found."""
    java = find_java()
    if not os.path.isfile(FREEROUTING_JAR):
        raise SystemExit(f"FreeRouting jar not found at {FREEROUTING_JAR} - see README")
    cmd = [java, "-jar", FREEROUTING_JAR, "-de", DSN, "-do", SES,
           "-mp", str(MAX_PASSES), "-oit", str(OPTIMIZATION_IMPROVEMENT_THRESHOLD),
           "--gui.enabled=false"]
    if not quiet:
        print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    # FreeRouting logs to stdout even on success - keep the tail, it's where
    # the final pass/route-completion summary shows up.
    tail = result.stdout.strip().splitlines()[-40:]
    if not quiet:
        print("\n".join(tail))
    if result.returncode != 0:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        raise SystemExit(f"FreeRouting failed (exit {result.returncode})")
    if not os.path.isfile(SES):
        raise SystemExit("FreeRouting exited OK but did not produce a .ses file")
    # FreeRouting's summary line carries a "(N unrouted)" only when N > 0 - on
    # a fully-routed board it just prints the score and stops. Treating a
    # missing count as "unknown" made every clean run look like a failure and
    # burned all the retries; a completed session with no count IS zero.
    m = None
    for line in result.stdout.splitlines():
        if "session completed" not in line:
            continue
        hit = re.search(r"\((\d+) unrouted\)", line)
        m = int(hit.group(1)) if hit else 0
    return m


# The autorouter is stochastic - it does randomized restarts, and this
# board's 0.5mm-pitch parts (the Verdin SODIMM-260 socket, the SN65DSI85-Q1
# bridge, the LM61460-Q1 VQFN-HR) sit at the edge of what it can solve in one shot, same
# regime thermo-pcb hit with its own 0.5mm-pitch VQFN. Retrying is what
# actually converges: keep the first clean result, and if none of the
# attempts is clean, keep the best one and say so loudly rather than leaving
# a silently-incomplete board behind.
MAX_ROUTE_ATTEMPTS = 6


def route_until_clean():
    best_unrouted, best_ses = None, None
    for attempt in range(1, MAX_ROUTE_ATTEMPTS + 1):
        unrouted = run_freerouting(quiet=(attempt > 1))
        print(f"route attempt {attempt}/{MAX_ROUTE_ATTEMPTS}: "
              f"{unrouted if unrouted is not None else '?'} unrouted")
        if unrouted == 0:
            return 0
        if unrouted is not None and (best_unrouted is None or unrouted < best_unrouted):
            best_unrouted = unrouted
            best_ses = open(SES, encoding="utf-8").read()
    if best_ses is not None:
        open(SES, "w", encoding="utf-8").write(best_ses)
    print(f"WARNING: no fully-routed result in {MAX_ROUTE_ATTEMPTS} attempts - "
          f"keeping the best ({best_unrouted} unrouted). The remaining "
          f"connections need hand-routing in pcbnew before this board is real.")
    return best_unrouted


def import_ses():
    run_kicad_python("SES import", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ImportSpecctraSES(board, {SES!r})
print("SES import:", "OK" if ok else "FAILED")
if not ok:
    raise SystemExit(1)
board.Save({PCB!r})
print("Saved routed board to", {PCB!r})
''')


ZONE_INSET_MM = 0.5   # clearance from Edge.Cuts
# GND on both inner layers next to the outer ones - see the layer plan in
# the header. In2/In3 are left to the router.
ZONES = [("GND", "In1_Cu"), ("GND", "In4_Cu")]


def add_and_fill_zones():
    # Deliberately done AFTER routing is complete, not before - same lesson
    # thermo-pcb's own history already paid for: adding the GND zone
    # OUTLINES before routing makes FreeRouting's DSN "plane" mechanism treat
    # In1.Cu/In2.Cu as pre-claimed, which (1) makes an already-tight board
    # noticeably harder to fully autoroute, since a solid zone removes a
    # WHOLE layer's normal "just drop a via here" flexibility everywhere, not
    # only where the plane's own net needs it, and (2) can report "100%
    # routed" while a pad is only connected via a plane fill that, once
    # actually computed with real clearance to nearby copper, doesn't quite
    # reach it.
    #
    # Fix: route everything as ordinary traces first, THEN add the zones as
    # purely ADDITIVE copper on top of an already-100%-connected board. Any
    # GND trace or via the zone happens to overlap just becomes
    # redundant (harmless) rather than being the ONLY connection.
    #
    # Solid (not thermal-relief) pad connections: no spoke geometry to fail,
    # and this board isn't hand-soldered at a scale where thermal relief's
    # easier-rework benefit matters more than connection reliability.
    #
    # ONE ZONE PER SUBPROCESS CALL, not both in one script: thermo-pcb
    # confirmed filling two zones on two different layers in the SAME
    # pcbnew process reliably SEGFAULTS on the subsequent board.Save() (one
    # zone at a time, identical settings, never does) - looks like a real
    # threading/state bug in this KiCad build's zone filler under scripting.
    # Independent load-add-fill-save cycles, each in its own process,
    # sidesteps it entirely: the second cycle loads the file the first cycle
    # already saved (with its zone intact) and adds/fills only the new one.
    for net_name, layer_name in ZONES:
        run_kicad_python(f"Add + fill {net_name} zone ({layer_name})", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
bbox = board.GetBoardEdgesBoundingBox()
inset = pcbnew.FromMM({ZONE_INSET_MM})
x0, y0 = bbox.GetLeft() + inset, bbox.GetTop() + inset
x1, y1 = bbox.GetRight() - inset, bbox.GetBottom() - inset

net = board.FindNet({net_name!r})
if net is None:
    raise SystemExit(f"net {net_name!r} not found on board")
zone = pcbnew.ZONE(board)
zone.SetLayer(pcbnew.{layer_name})
zone.SetNet(net)
zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
zone.SetLocalClearance(pcbnew.FromMM(0.2))
zone.SetMinThickness(pcbnew.FromMM(0.2))
outline = pcbnew.SHAPE_POLY_SET()
outline.NewOutline()
for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
    outline.Append(pcbnew.VECTOR2I(int(x), int(y)))
zone.SetOutline(outline)
board.Add(zone)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
board.Save({PCB!r})
print("Added + filled", {net_name!r}, "zone on", {layer_name!r}, "- saved to", {PCB!r})
''')


# ---------------------------------------------------------------------------
# Neck-down: widen the power trunks after routing
# ---------------------------------------------------------------------------
# Every net routes at the Default class's 0.2 mm, because that is what fits
# between the 0.5 mm-pitch pads of the SODIMM socket and the VQFN parts. That
# is fine for the last millimetre into a pad and not fine for a power rail
# that runs behind an ideal-diode controller and a 5 V buck. So do what a
# person hand-routing would: leave the escape narrow, widen everything past
# it. A chunk is widened only if it still clears every other pad, via and
# track by the wider width plus clearance. FreeRouting cannot express this
# itself (one width per net class), which is the only reason it happens here.
#
# Verified, not assumed: run_drc.py re-checks with KiCad's own engine, so a
# widening that created a real conflict shows up as a clearance violation.
#
# 0.2 and not the netclass "clearance" field: kicad-cli enforces the larger
# of that and the board minimum, and cluster-pcb's first route+widen pass
# produced 235 real violations from trusting the smaller number.
TRACK_CLEARANCE = 0.2


def widen_trunks():
    # The ladder and net list live in build_pcb.py so the routing width and
    # the final width are defined next to each other; read them out rather
    # than keeping a second copy here that could drift.
    src = open(os.path.join(HERE, "build_pcb.py"), encoding="utf-8").read()
    ladder_m = re.search(r"^TRUNK_WIDTH_LADDER = (\[.*?\])", src, re.S | re.M)
    nets_m = re.search(r"^TRUNK_NETS = (\[.*?\])", src, re.S | re.M)
    if not (ladder_m and nets_m):
        raise SystemExit("build_pcb.py no longer defines TRUNK_WIDTH_LADDER/TRUNK_NETS")
    ladder = ast.literal_eval(ladder_m.group(1))
    trunk_nets = ast.literal_eval(nets_m.group(1))

    run_kicad_python("Trunk widening", f'''
import math
import pcbnew

LADDER = {ladder!r}
TRUNK_NETS = set({trunk_nets!r})
TRACK_CLEARANCE = {TRACK_CLEARANCE!r}

board = pcbnew.LoadBoard({PCB!r})
MM = pcbnew.ToMM

def seg_point_dist(ax, ay, bx, by, px, py):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def seg_seg_dist(a1, a2, b1, b2):
    # No proper intersection test: two copper segments that genuinely cross
    # are either the same net (fine) or already a DRC error the router
    # wouldn't have produced. Endpoint-to-segment minimum is what matters for
    # the near-miss case this is actually guarding.
    return min(
        seg_point_dist(a1[0], a1[1], a2[0], a2[1], b1[0], b1[1]),
        seg_point_dist(a1[0], a1[1], a2[0], a2[1], b2[0], b2[1]),
        seg_point_dist(b1[0], b1[1], b2[0], b2[1], a1[0], a1[1]),
        seg_point_dist(b1[0], b1[1], b2[0], b2[1], a2[0], a2[1]))

# Build the full obstacle list ONCE: every pad, via and track on the board,
# with the layers it occupies, its net, and a half-extent. This checks
# against everything on the board, not just the fine-pitch parts, because
# what matters is "does this segment, once wider, still clear everything
# around it" - not "is this segment inside a known pad escape region".
pads, vias, tracks = [], [], []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        pos = pad.GetPosition()
        sz = pad.GetSize()
        pads.append((MM(pos.x), MM(pos.y),
                     math.hypot(MM(sz.x), MM(sz.y)) / 2,
                     pad.GetNetCode(), set(pad.GetLayerSet().Seq())))
for t in board.GetTracks():
    if t.GetClass() == "PCB_VIA":
        pos = t.GetPosition()
        # GetWidth() with NO layer argument trips a wxWidgets assert inside
        # PCB_VIA in KiCad 10 ("called without a layer argument"). Under
        # kicad-cli's headless python that assert does not raise and does not
        # print - it BLOCKS, forever, with the process sitting at 0% CPU.
        # Always pass the layer (thermo-pcb paid for this finding once).
        vias.append((MM(pos.x), MM(pos.y), MM(t.GetWidth(pcbnew.F_Cu)) / 2,
                     t.GetNetCode(), None))          # None = all layers
    else:
        s, e = t.GetStart(), t.GetEnd()
        tracks.append((t, (MM(s.x), MM(s.y)), (MM(e.x), MM(e.y)),
                       MM(t.GetWidth()) / 2, t.GetNetCode(), t.GetLayer()))
print("obstacles:", len(pads), "pads,", len(vias), "vias,", len(tracks), "tracks")

def can_widen(track, s, e, layer, netcode, target):
    half = target / 2
    for px, py, pr, pnet, players in pads:
        if pnet == netcode or (players is not None and layer not in players):
            continue
        if seg_point_dist(s[0], s[1], e[0], e[1], px, py) < half + pr + TRACK_CLEARANCE:
            return False
    for vx, vy, vr, vnet, _ in vias:
        if vnet == netcode:
            continue
        if seg_point_dist(s[0], s[1], e[0], e[1], vx, vy) < half + vr + TRACK_CLEARANCE:
            return False
    for ot, os_, oe, ohalf, onet, olayer in tracks:
        if onet == netcode or olayer != layer or ot is track:
            continue
        if seg_seg_dist(s, e, os_, oe) < half + ohalf + TRACK_CLEARANCE:
            return False
    return True

# Split trunk traces into short chunks BEFORE deciding widths - the
# difference between neck-down working and not working at all. The router
# emits each trace as a few long segments; testing a whole long segment as
# one unit means a pinch at one end vetoes widening the rest of it. Chunking
# makes the decision local, so the escape stays thin and the run past it
# fattens up.
CHUNK_MM = 0.4
made = 0
for track, s, e, half, netcode, layer in tracks:
    if track.GetNetname() not in TRUNK_NETS:
        continue
    length = math.hypot(e[0] - s[0], e[1] - s[1])
    n = max(1, int(math.ceil(length / CHUNK_MM)))
    if n == 1:
        made += 1
        continue
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        a = (s[0] + (e[0] - s[0]) * t0, s[1] + (e[1] - s[1]) * t0)
        b = (s[0] + (e[0] - s[0]) * t1, s[1] + (e[1] - s[1]) * t1)
        nt = pcbnew.PCB_TRACK(board)
        nt.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
        nt.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b[0]), pcbnew.FromMM(b[1])))
        nt.SetWidth(track.GetWidth())
        nt.SetLayer(layer)
        nt.SetNetCode(netcode)
        board.Add(nt)
        made += 1
    board.Remove(track)
print("split trunk traces into", made, "chunks at", CHUNK_MM, "mm")

# The obstacle list still holds the ORIGINAL long segments, which would now
# veto their own replacements. Rebuild it from what is actually on the
# board. Entries are LISTS, not tuples, because a chunk that gets widened
# has to be seen at its NEW width by every chunk tested after it -
# otherwise two neighbouring nets could each widen against the other's
# stale narrow width and end up genuinely too close, with nothing catching
# it until fab. (SWIG's PCB_TRACK is not hashable, so no dict keyed by
# track - the widening loop below just iterates the obstacle entries
# themselves and mutates them in place, which needs no lookup at all.)
tracks = []
trunk_entries = []
for t in board.GetTracks():
    if t.GetClass() == "PCB_VIA":
        continue
    s2, e2 = t.GetStart(), t.GetEnd()
    entry = [t, (MM(s2.x), MM(s2.y)), (MM(e2.x), MM(e2.y)),
             MM(t.GetWidth()) / 2, t.GetNetCode(), t.GetLayer()]
    tracks.append(entry)
    if t.GetNetname() in TRUNK_NETS:
        trunk_entries.append(entry)

hist = {{}}
total_len = {{}}
for entry in trunk_entries:
    track, s, e, half, netcode, layer = entry
    seg_len = math.hypot(e[0] - s[0], e[1] - s[1])
    chosen = half * 2
    for target in LADDER:
        if target <= chosen + 1e-6:
            break
        if can_widen(track, s, e, layer, netcode, target):
            track.SetWidth(pcbnew.FromMM(target))
            entry[3] = target / 2      # keep the obstacle list honest
            chosen = target
            break
    key = round(chosen, 2)
    hist[key] = hist.get(key, 0) + 1
    total_len[key] = total_len.get(key, 0.0) + seg_len

board.Save({PCB!r})
grand = sum(total_len.values()) or 1.0
print("Trunk widening, final width distribution on", sorted(TRUNK_NETS), ":")
for w in sorted(hist, reverse=True):
    print(f"  {{w:.2f}}mm: {{hist[w]:3d}} segments, {{total_len[w]:6.1f}}mm "
          f"({{100 * total_len[w] / grand:4.1f}}% of trunk length)")
''')


# Real ceiling on full-pipeline retries (each one regenerates a fresh
# unrouted board and re-runs FreeRouting from scratch) - separate from
# MAX_ROUTE_ATTEMPTS, which only covers FreeRouting's own retries within
# ONE pipeline pass. Needed because FreeRouting's own self-reported
# "unrouted" count isn't always the real ground truth (see
# real_unconnected_count()'s own comment) - when it's wrong, no amount
# of retrying FreeRouting alone fixes it, since route_until_clean()
# already believed it succeeded and stopped.
MAX_PIPELINE_ATTEMPTS = 3


def regenerate_unrouted_board():
    build_pcb = os.path.join(HERE, "build_pcb.py")
    result = subprocess.run([sys.executable, build_pcb], capture_output=True, text=True)
    print(result.stdout.strip().splitlines()[-1] if result.stdout else "")
    if result.returncode != 0:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        raise SystemExit("build_pcb.py failed while regenerating a fresh unrouted board")


def board_has_copper():
    """True if the board already carries routed copper or zones."""
    txt = open(PCB, encoding="utf-8").read()
    return "(segment" in txt or "(via" in txt or "(zone" in txt


# Differential pairs worth measuring. FreeRouting does not tune length, so
# this only reports; it does not fix anything. Via length is not counted.
PAIR_PREFIXES = ["DSI_D0", "DSI_D1", "DSI_D2", "DSI_D3", "DSI_CLK",
                 "LVDS_A_Y0", "LVDS_A_Y1", "LVDS_A_Y2", "LVDS_A_Y3", "LVDS_A_CLK",
                 "LVDS_B_Y0", "LVDS_B_Y1", "LVDS_B_Y2", "LVDS_B_Y3", "LVDS_B_CLK",
                 "USB1_D", "CAN1_"]


def report_pair_skew():
    run_kicad_python("Pair skew report", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
length = {{}}
for t in board.GetTracks():
    if t.GetClass() == "PCB_VIA":
        continue
    length[t.GetNetname()] = length.get(t.GetNetname(), 0) + pcbnew.ToMM(t.GetLength())

def pair(p, n):
    if p in length and n in length:
        return length[p], length[n]
pairs = []
for name in sorted(length):
    for suffix_p, suffix_n in (("_P", "_N"), ("P", "N"), ("_H", "_L"), ("DP", "DN")):
        if name.endswith(suffix_p):
            other = name[:-len(suffix_p)] + suffix_n
            if other in length:
                pairs.append((name[:-len(suffix_p)].rstrip("_") or name, length[name], length[other]))
seen = set()
print("intra-pair skew (routed copper only, vias not counted):")
for base, a, b in pairs:
    if base in seen or not any(base.startswith(pf) or pf in base for pf in {PAIR_PREFIXES!r}):
        continue
    seen.add(base)
    print(f"  {{base:14s}} {{a:7.2f}} / {{b:7.2f}} mm   skew {{abs(a - b):5.2f}} mm")
''')


if __name__ == "__main__":
    if board_has_copper():
        print("Board already has copper on it - regenerating a fresh unrouted "
              "board first (re-routing a filled board hangs FreeRouting).")
        regenerate_unrouted_board()
    for pipeline_attempt in range(1, MAX_PIPELINE_ATTEMPTS + 1):
        export_dsn()
        route_until_clean()
        import_ses()
        widen_trunks()
        add_and_fill_zones()
        real_unconnected = real_unconnected_count()
        if real_unconnected == 0:
            print(f"\nReal DRC confirms 0 unconnected items (pipeline attempt "
                  f"{pipeline_attempt}/{MAX_PIPELINE_ATTEMPTS}).")
            report_pair_skew()
            break
        print(f"\nWARNING: FreeRouting/route_until_clean() reported success, but "
              f"kicad-cli's own real DRC found {real_unconnected} unconnected "
              f"item(s) (pipeline attempt {pipeline_attempt}/{MAX_PIPELINE_ATTEMPTS}) - "
              f"the self-reported count was wrong. ", end="")
        if pipeline_attempt < MAX_PIPELINE_ATTEMPTS:
            print("Regenerating a fresh unrouted board and trying the whole "
                  "pipeline again.")
            regenerate_unrouted_board()
        else:
            print(f"Out of pipeline attempts - this board has "
                  f"{real_unconnected} real unrouted connection(s) left; "
                  f"hand-route them in pcbnew before this board is real.")
    print("\nDone. Run: python run_drc.py")
