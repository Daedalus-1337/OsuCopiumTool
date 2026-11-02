"""osu! replay accuracy analyser.

Pairs replay taps with beatmap notes using a note-lock model, calibrates the
replay's time axis against the game's own recorded 300/100/50/miss counts, then
reports where the hit error comes from:

  * global timing bias (late/early) and jitter
  * error per input button, so a two-hand offset is visible
  * error inside fast stream runs vs the rest of the map
  * hand phase inside streams: which hand is late on its turn
  * drift across a run, dropped notes, near-simultaneous presses
  * what-if projections: accuracy with the bias removed, or with stream timing
    as tight as the rest of the map

Usage:
    python analyze.py <replay.osr> <beatmap.osu> [outdir]
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import osr_parse
import osu_beatmap

BUTTON_NAMES = {1: "M1", 2: "M2", 4: "K1", 8: "K2"}
BUTTON_LONG = {
    1: "M1  mouse-left / pen tip",
    2: "M2  mouse-right",
    4: "K1  keyboard key 1",
    8: "K2  keyboard key 2",
}
COLORS = {1: "#d62728", 2: "#1f77b4", 4: "#2ca02c", 8: "#9467bd"}
MIN_PRESS_GAP = 5.0        # ms; models the game's doubletap filter
W300 = W100 = W50 = 0.0


# ---------------------------------------------------------------- parsing ---
def hit_windows(od, mods=0):
    if mods & 2:
        od /= 2
    if mods & 16:
        od = min(10.0, od * 1.4)
    od = max(0.0, min(10.0, od))
    return 80 - 6 * od, 140 - 8 * od, 200 - 10 * od


def rate_from_mods(mods):
    if mods & (64 | 512):
        return 1.5
    if mods & 256:
        return 0.75
    return 1.0


def speed_from_mods(mods):
    """How fast the map clock runs relative to wall time (DT/NC 1.5, HT 0.75).

    Used only to convert units: judge() works on the map clock, but the hit
    windows are also defined on the map clock, so a DT play has the same window
    in map-milliseconds while the map runs 1.5x faster. Dividing both the errors
    and the windows by this factor puts every play into real (wall-clock)
    milliseconds, which is the only way DT and NM plays can be compared. The
    verdicts themselves are unchanged."""
    if mods & (64 | 512):
        return 1.5
    if mods & 256:
        return 0.75
    return 1.0


def press_events(rep):
    out, prev = [], 0
    for f in rep.frames:
        ch = f.keys ^ prev
        for bit in BUTTON_NAMES:
            if ch & bit and (f.keys & bit):
                out.append((f.t, bit))
        prev = f.keys
    out.sort()
    return out


def hold_spans(rep):
    spans, prev, start = [], 0, {}
    for f in rep.frames:
        ch = f.keys ^ prev
        for bit in BUTTON_NAMES:
            if ch & bit:
                if f.keys & bit:
                    start[bit] = f.t
                elif bit in start:
                    spans.append((start.pop(bit), f.t, bit))
        prev = f.keys
    for bit, s in start.items():
        spans.append((s, rep.frames[-1].t, bit))
    return spans


# ---------------------------------------------------------------- judging ---
def classify(e):
    a = abs(e)
    if not np.isfinite(e):
        return "miss"
    if a <= W300:
        return "300"
    if a <= W100:
        return "100"
    if a <= W50:
        return "50"
    return "miss"


def judge(note_times, presses, delta=0.0, min_gap=MIN_PRESS_GAP):
    """note-lock model: notes are consumed in order; a note whose window has
    closed before the next press is a miss; a press then hits the oldest
    unjudged note if it is inside its window, else it is stray."""
    n = np.asarray(note_times, dtype=float) - delta
    N = len(n)
    err = np.full(N, np.nan)
    btn = np.zeros(N, dtype=int)
    hit = np.zeros(N, dtype=bool)
    press_t = np.full(N, np.nan)
    idx, last, stray = 0, -1e9, 0
    stray_t = []
    for tp, bit in presses:
        while idx < N and tp - n[idx] > W50:
            idx += 1
        if tp - last < min_gap:
            stray += 1
            stray_t.append(tp)
            continue
        if idx < N and abs(tp - n[idx]) <= W50:
            err[idx] = tp - n[idx]
            btn[idx] = bit
            hit[idx] = True
            press_t[idx] = tp
            idx += 1
            last = tp
        else:
            stray += 1
            stray_t.append(tp)
    return err, btn, hit, press_t, stray, np.array(stray_t)


def counts_from(verdict, extra_300=0):
    c = {"300": 0, "100": 0, "50": 0, "miss": 0}
    for v in verdict:
        c[v] += 1
    c["300"] += extra_300
    return c


def calibrate(raw_note_times, presses, od, mods, game, n_spin, rates=(1.0, 1.5, 0.75)):
    """Find the time axis and clock offset that best reproduce the counts the
    game itself recorded. Search over a speed factor (in case a client writes
    replay timestamps in real rather than map time) and a small clock offset.
    Returns (rate, delta, residual, counts)."""
    global W300, W100, W50
    W300, W100, W50 = hit_windows(od, mods)
    raw = np.asarray(raw_note_times, dtype=float)
    best = None
    for rate in rates:
        nt = raw / rate
        for delta in np.arange(-25.0, 25.5, 0.5):
            e, _b, _h, _p, _s, _st = judge(nt, presses, delta=float(delta))
            c = counts_from([classify(x) for x in e], n_spin)
            sc = sum(abs(c[k] - game[k]) for k in game)
            if best is None or sc < best[0]:
                best = (sc, float(rate), float(delta), c)
    return best[1], best[2], best[0], best[3]


def acc_of(c):
    n = sum(c.values())
    return (300 * c["300"] + 100 * c["100"] + 50 * c["50"]) / (300 * n) * 100 if n else 0.0


def acc_for_errors(all_err, n_spin=0):
    """accuracy implied by a per-note error array (nan = surely missed)."""
    v = [classify(e) for e in all_err]
    return acc_of(counts_from(v, n_spin))


def describe(label, values, width=32):
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if not len(v):
        return f"{label:<{width}} (no samples)"
    return (f"{label:<{width}} n={len(v):4d}  bias {v.mean():+6.1f}  median {np.median(v):+6.1f}"
            f"  sd {v.std():5.1f}  p5 {np.percentile(v,5):+5.0f}  p95 {np.percentile(v,95):+6.0f}")


def build_runs(note_t, fast_thr):
    """maximal runs of consecutive notes spaced below the stream threshold."""
    gaps = np.diff(note_t, prepend=note_t[0] - 1e9)
    runs, cur = [], []
    for i in range(1, len(note_t)):
        if gaps[i] < fast_thr:
            if not cur:
                cur = [i - 1]
            cur.append(i)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs, gaps


def main():
    global W300, W100, W50
    osr_path, osu_path = sys.argv[1], sys.argv[2]
    outdir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("out")
    outdir.mkdir(parents=True, exist_ok=True)

    rep = osr_parse.load(osr_path)
    bm = osu_beatmap.parse(osu_path)
    objs = bm.objects
    tap_idx = [i for i, o in enumerate(objs) if not o.is_spinner]
    n_spin = len(objs) - len(tap_idx)
    raw_t = np.array([objs[i].time for i in tap_idx])
    note_kind = np.array(["circle" if objs[i].is_circle else "slider" for i in tap_idx])
    presses = press_events(rep)
    holds = hold_spans(rep)
    game = {"300": rep.count_300, "100": rep.count_100, "50": rep.count_50,
            "miss": rep.count_miss}

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    # ---- time axis + clock calibration against the game's own counts ------
    rate, delta, residual, mod_c = calibrate(raw_t, presses, bm.od, rep.mods,
                                             game, n_spin)
    W300, W100, W50 = hit_windows(bm.od, rep.mods)
    note_t = raw_t / rate
    err, btn, hit, press_t, stray, stray_t = judge(note_t, presses, delta=delta)

    # convert the map clock to wall-clock milliseconds (see speed_from_mods)
    speed = speed_from_mods(rep.mods)
    if abs(speed - 1.0) > 1e-9:
        err = err / speed
        press_t = press_t / speed
        note_t = note_t / speed
        W300, W100, W50 = W300 / speed, W100 / speed, W50 / speed
    _, gaps = build_runs(note_t, 1e9)

    out("=" * 80)
    out(f"{rep.username}  |  {bm.artist} - {bm.title} [{bm.version}]  by {bm.creator}")
    out(f"mods {rep.mods_str}   AR{bm.ar} OD{bm.od} CS{bm.cs} HP{bm.hp}"
        f"   played {rep.played_at}")
    units = "wall-clock ms" + (f" (map clock divided by {speed:g} for the mods)"
                              if abs(speed - 1.0) > 1e-9 else "")
    out(f"hit windows (half-width): 300 +-{W300:.1f}  100 +-{W100:.1f}  50 +-{W50:.1f} {units}")
    out(f"objects {len(objs)}  ({len(tap_idx)} tap-judged + {n_spin} spinners)"
        f"   replay frames {len(rep.frames)}   presses {len(presses)}")
    out("=" * 80)
    out()
    out("VALIDATION")
    out(f"  replay recorded        : 300 {game['300']}  100 {game['100']}  50 {game['50']}"
        f"  miss {game['miss']}   acc {rep.acc:.2f}%")
    out(f"  model (clock {delta:+.1f} ms)  : 300 {mod_c['300']}  100 {mod_c['100']}"
        f"  50 {mod_c['50']}  miss {mod_c['miss']}   acc {acc_of(mod_c):.2f}%")
    out(f"  residual |count error|  : {residual}")
    expect = rate_from_mods(rep.mods)
    if abs(rate - 1.0) < 1e-6:
        axis = ("replay timestamps are on the beatmap clock (map time), so no speed"
                " correction is applied")
        if abs(expect - 1.0) > 1e-6:
            axis += (f" - note the mods imply x{expect}, which confirms the client writes"
                     f" map time rather than real time")
    else:
        axis = f"replay timestamps needed a x{rate} correction to line up"
    out(f"  time axis               : {axis}")
    out(f"  clock calibration offset {delta:+.1f} ms - the offset that reproduces the"
        f" recorded counts, so the errors below are the ones the game judged")
    out("  caveat: a clock offset and a client hit-window difference are not separately")
    out("          identifiable from a score header (fitting both at once swings the offset")
    out("          over +/-20 ms). Treat the ABSOLUTE bias as good to roughly +/-10-20 ms.")
    out("          Comparisons between groups inside one play (fast vs slow notes, one hand")
    out("          vs the other, in-run phase) shift together and are unaffected.")
    out(f"  stray presses           : {stray}   (no note inside the 50-window, or inside the"
        f" {MIN_PRESS_GAP:.0f} ms doubletap filter)")
    out()

    m = hit
    errors = err[m]
    buttons = btn[m]
    kinds = note_kind[m]
    g_m = gaps[m]
    ci = np.arange(len(note_t))[m]          # note index of each matched tap

    out("GLOBAL TIMING")
    out(describe("all matched taps", errors))
    out(f"  late {100*(errors>0).mean():.1f}%   early {100*(errors<0).mean():.1f}%"
        f"   beyond the 300 window {100*(np.abs(errors)>W300).mean():.1f}%"
        f"   beyond the 50 window {100*(np.abs(errors)>W50).mean():.2f}%")
    sigma = errors.std()
    ideal = 100 * (2 * 0.5 * (1 + math.erf((W300 / sigma) / math.sqrt(2))) - 1)
    out(f"  jitter sd {sigma:.1f} ms vs a {W300:.1f} ms 300 window"
        f"  -> with this sd but zero bias, ~{ideal:.1f}% of taps would be 300s"
        f" (currently {100*(np.abs(errors)<=W300).mean():.1f}%)")
    out()

    out("PER INPUT BUTTON")
    for bit in sorted(set(buttons.tolist())):
        sel = errors[buttons == bit]
        out(describe(BUTTON_LONG[bit], sel))
        out(f"{'':<32}   late {100*(sel>0).mean():4.1f}%   |err|>50ms {100*(np.abs(sel)>50).mean():4.1f}%"
            f"   300-rate {100*(np.abs(sel)<=W300).mean():5.1f}%")
    for bit in sorted(BUTTON_NAMES):
        n_ = int(sum(1 for _, b in presses if b == bit))
        if n_:
            hl = [e - s for s, e, b in holds if b == bit]
            out(f"  {BUTTON_NAMES[bit]:>2} total presses {n_:4d}"
                f"   share {100*n_/len(presses):5.1f}%"
                f"   mean hold {np.mean(hl):5.0f} ms" if hl else
                f"  {BUTTON_NAMES[bit]:>2} total presses {n_:4d}")
    out()

    # ---- map pattern structure -------------------------------------------
    rv = np.round(gaps[1:])
    vals, cnts = np.unique(rv, return_counts=True)
    common = [(int(v), int(c)) for v, c in zip(vals, cnts) if c >= max(5, 0.04 * len(gaps))]
    gmin = min((v for v, c in common), default=100.0)
    fast_thr = 1.3 * gmin
    fast = gaps < fast_thr
    fast[0] = False
    runs, _ = build_runs(note_t, fast_thr)

    same_iv, prev_b, prevt = [], None, None
    for tp, bit in presses:
        if bit == prev_b and prevt is not None:
            same_iv.append(tp - prevt)
        prev_b, prevt = bit, tp
    same_iv = np.array(same_iv) if same_iv else np.array([120.0])
    hand_floor = float(np.percentile(same_iv, 5))

    out("MAP PATTERN STRUCTURE")
    out(f"  snap grid (>=4% of gaps): "
        f"{', '.join(f'{v} ms x{c}' for v, c in sorted(common, key=lambda x: -x[1])[:6])}")
    out(f"  stream threshold        : notes spaced < {fast_thr:.0f} ms")
    out(f"  notes at stream spacing : {int(fast.sum())} of {len(note_t)}"
        f" ({100*fast.mean():.1f}%)")
    if runs:
        ln = [len(r) for r in runs]
        hist = {}
        for L in ln:
            hist[L] = hist.get(L, 0) + 1
        out(f"  stream runs             : {len(runs)} runs covering {int(sum(ln))} notes,"
            f" longest {max(ln)}, median {int(np.median(ln))}")
        out(f"  run length histogram    : {dict(sorted(hist.items()))}")
    out(f"  measured same-button tap intervals: min {same_iv.min():.0f} ms,"
        f" p5 {hand_floor:.0f} ms  (one hand's speed ceiling)")
    out(f"  notes spaced faster than that ceiling: {int((gaps[1:] < hand_floor).sum())}"
        f"  - impossible to hit one-handed")
    out()

    out("ERROR BY CONTEXT")
    out(describe("slow notes", errors[g_m >= fast_thr]))
    out(describe("stream notes", errors[g_m < fast_thr]))
    out(describe("circles", errors[kinds == "circle"]))
    out(describe("slider heads", errors[kinds == "slider"]))
    out()
    out("  error vs spacing to the previous note:")
    for lo, hi in ((0, 60), (60, 86), (86, 120), (120, 160), (160, 250), (250, 320), (320, 1e9)):
        sel = errors[(g_m >= lo) & (g_m < hi)]
        if len(sel):
            top = f"{hi:.0f}" if hi < 1e9 else "inf"
            out(f"    gap {lo:4.0f}-{top:>4} ms  n={len(sel):4d}  bias {sel.mean():+6.1f}"
                f"  sd {sel.std():5.1f}  300-rate {100*(np.abs(sel)<=W300).mean():5.1f}%")
    out()

    # ---- hand phase inside streams ---------------------------------------
    out("HAND PHASE INSIDE STREAMS")
    if runs:
        # err / btn / press_t are all indexed by note index
        devs = []
        for r in runs:
            ids = [i for i in r if hit[i]]
            if len(ids) < 2:
                continue
            ts = press_t[ids]
            g = float(np.mean(np.diff(note_t[r])))
            devs.extend(((np.diff(ts) / g) - 1.0).tolist())
        devs = np.array(devs)
        if len(devs):
            out(f"  in-run tap interval, deviation from the grid (%):"
                f" p10 {100*np.percentile(devs,10):+6.0f}  p25 {100*np.percentile(devs,25):+6.0f}"
                f"  median {100*np.median(devs):+6.0f}  p75 {100*np.percentile(devs,75):+6.0f}"
                f"  p90 {100*np.percentile(devs,90):+6.0f}")
            out(f"  in-run intervals within +-15% of the grid: {100*(np.abs(devs)<=0.15).mean():.1f}%"
                f"   (a locked alternation sits here almost always)")
            out(f"  in-run intervals more than 50% off the grid: {100*(np.abs(devs)>0.50).mean():.1f}%")
        offs = []
        for r in runs:
            ids = [i for i in r if hit[i]]
            if not ids:
                continue
            e, b = err[ids], btn[ids]
            if (b == 1).sum() >= 2 and (b == 2).sum() >= 2:
                offs.append(np.median(e[b == 2]) - np.median(e[b == 1]))
        if offs:
            offs = np.array(offs)
            out(f"  per-run inter-hand offset (median M2 err - median M1 err):"
                f" median {np.median(offs):+.1f} ms over {len(offs)} runs"
                f"   p25 {np.percentile(offs,25):+.1f}   p75 {np.percentile(offs,75):+.1f}")
        alt_rate = []
        for r in runs:
            s = [int(btn[i]) for i in r if hit[i]]
            if len(s) > 1:
                alt_rate.append(sum(1 for x, y in zip(s, s[1:]) if x != y) / (len(s) - 1))
        if alt_rate:
            out(f"  alternation rate within runs: mean {100*np.mean(alt_rate):.1f}%,"
                f" runs with 100% alternation {int(sum(1 for a in alt_rate if a == 1.0))}"
                f" of {len(alt_rate)}")
    out()

    # ---- within-run behaviour --------------------------------------------
    out("WITHIN-RUN BEHAVIOUR")
    if runs:
        drifts, cvs, nsim, nint = [], [], 0, 0
        for r in runs:
            ids = [i for i in r if hit[i]]
            if len(ids) < 3:
                continue
            e = err[ids]
            drifts.append(np.polyfit(np.arange(len(e)), e, 1)[0])
            ts = press_t[ids]
            iv = np.diff(ts)
            if iv.mean():
                cvs.append(iv.std() / iv.mean())
            nsim += int((iv < 25).sum())
            nint += len(iv)
        if drifts:
            drifts = np.array(drifts)
            out(f"  drift across a run (ms gained per note): median {np.median(drifts):+.1f},"
                f" p25 {np.percentile(drifts,25):+.1f}, p75 {np.percentile(drifts,75):+.1f}")
            out(f"    positive = the player drifts progressively later as the run goes on")
        if cvs:
            out(f"  tap-interval CV inside runs: median {np.median(cvs):.3f}"
                f"   (locked alternation is ~0.05-0.10; 0 = perfectly even)")
        out(f"  near-simultaneous taps (<25 ms apart): {nsim} of {nint} in-run intervals"
            f" ({100*nsim/max(1,nint):.1f}%) - a note dropped and the pair re-pressed")
    out()

    # ---- what-if ---------------------------------------------------------
    out("WHAT-IF PROJECTIONS (arithmetic, from the player's own error distribution)")
    base = acc_of(mod_c)
    shift = -np.median(errors)
    shifted = err + shift
    a_acc = acc_for_errors(shifted, n_spin)
    fmask = g_m < fast_thr
    slow_e = errors[~fmask]
    tight = err.copy()
    if fmask.any() and len(slow_e) > 4 and errors[fmask].std() > 0:
        mu_f, sd_f = errors[fmask].mean(), errors[fmask].std()
        mu_s, sd_s = slow_e.mean(), slow_e.std()
        tight[m] = np.where(fmask, (errors - mu_f) / sd_f * sd_s + mu_s, errors)
    b_acc = acc_for_errors(tight, n_spin)
    both = tight + shift
    ab_acc = acc_for_errors(both, n_spin)
    out(f"  as played                                     {base:6.2f}%")
    out(f"  global lateness removed ({shift:+.0f} ms)            {a_acc:6.2f}%"
        f"   ({a_acc-base:+5.2f})")
    if len(slow_e) > 4:
        out(f"  stream timing as tight as the rest of the map {b_acc:6.2f}%"
            f"   ({b_acc-base:+5.2f})")
        out(f"  both                                          {ab_acc:6.2f}%"
            f"   ({ab_acc-base:+5.2f})")
    out()

    # ---- plots -----------------------------------------------------------
    plots = outdir / "plots"
    plots.mkdir(exist_ok=True)
    made = []

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    bins = np.arange(-120, 121, 4)
    for bit in sorted(set(buttons.tolist())):
        sel = errors[buttons == bit]
        if len(sel) < 3:
            continue
        ax.hist(sel, bins=bins, histtype="step", lw=1.8,
                label=f"{BUTTON_NAMES[bit]} (n={len(sel)})", color=COLORS.get(bit))
    ax.axvspan(-W300, W300, color="green", alpha=0.10, label=f"+/-{W300:.1f} ms = 300 window")
    ax.axvline(0, color="k", lw=0.8)
    ax.axvline(np.median(errors), color="k", ls="--", lw=1.2,
               label=f"median {np.median(errors):+.0f} ms")
    ax.set_xlabel("hit error (ms)    negative = early, positive = late")
    ax.set_ylabel("notes")
    ax.set_title(f"hit error distribution - {bm.title} [{bm.version}]")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = plots / "hit_errors.png"
    fig.savefig(p); plt.close(fig); made.append(p)

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    xs, mds, lo, hi = [], [], [], []
    for a, b_ in ((0, 60), (60, 86), (86, 120), (120, 160), (160, 250), (250, 320), (320, 1e9)):
        sel = errors[(g_m >= a) & (g_m < b_)]
        if len(sel) < 3:
            continue
        xs.append(np.sqrt(max(a, 20) * min(b_, max(a, 20) * 4)))
        mds.append(np.median(sel))
        lo.append(np.percentile(sel, 10))
        hi.append(np.percentile(sel, 90))
    ax.fill_between(xs, lo, hi, color="grey", alpha=0.25, label="p10-p90")
    ax.plot(xs, mds, "o-", color="#d62728", label="median error")
    ax.axhline(0, color="k", lw=0.8)
    ax.axhspan(-W300, W300, color="green", alpha=0.10, label="300 window")
    ax.set_xscale("log")
    ax.set_xlabel("spacing to the previous note (ms)")
    ax.set_ylabel("hit error (ms)")
    ax.set_title("error grows as notes get closer together")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = plots / "error_vs_gap.png"
    fig.savefig(p); plt.close(fig); made.append(p)

    if runs:
        top = sorted(runs, key=len, reverse=True)[:3]
        fig, axes = plt.subplots(len(top), 1, figsize=(9, 2.2 * len(top)), dpi=130)
        axes = np.atleast_1d(axes)
        for ax, r in zip(axes, top):
            t0 = note_t[r[0]]
            for i in r:
                ax.axvline(note_t[i] - t0, color="k", lw=1.0, alpha=0.6)
            for i in r:
                if hit[i]:
                    ax.plot([press_t[i] - t0], [0.5], marker="v", ms=8,
                            color=COLORS.get(int(btn[i]), "grey"))
            ax.set_yticks([])
            ax.set_xlim(-25, note_t[r[-1]] - t0 + 70)
            ax.set_ylabel(f"{len(r)} notes\n{gmin:.0f} ms", fontsize=8)
            ax.set_title(f"stream at {t0/1000:.0f} s", fontsize=9)
        axes[0].plot([], [], "k|", label="note time")
        axes[0].plot([], [], "v", color="#d62728", label="M1 tap")
        axes[0].plot([], [], "v", color="#1f77b4", label="M2 tap")
        axes[0].legend(fontsize=7, ncol=3, loc="upper right")
        axes[-1].set_xlabel("time within the stream (ms)")
        fig.suptitle("streams: black ticks are the notes, triangles are the taps", fontsize=10)
        fig.tight_layout()
        p = plots / "stream_timelines.png"
        fig.savefig(p); plt.close(fig); made.append(p)

    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=130)
    tn = note_t[m] / 1000.0
    ax.scatter(tn, errors, s=5, alpha=0.35, c=[COLORS.get(int(b), "grey") for b in buttons])
    order = np.argsort(tn)
    k = max(5, len(tn) // 60)
    if len(tn) > k * 3:
        sm = np.convolve(errors[order], np.ones(k) / k, mode="valid")
        ax.plot(tn[order][k // 2:k // 2 + len(sm)], sm, color="k", lw=1.6,
                label=f"rolling mean ({k} notes)")
    ax.axhspan(-W300, W300, color="green", alpha=0.10)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("play time (s)")
    ax.set_ylabel("hit error (ms)")
    ax.set_title("hit error across the play (red = M1, blue = M2)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = plots / "error_timeline.png"
    fig.savefig(p); plt.close(fig); made.append(p)

    # ---- csv -------------------------------------------------------------
    pairs_csv = outdir / "pairs.csv"
    with pairs_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["note_index", "note_time_ms", "kind", "gap_prev", "press_time_ms",
                    "button", "error_ms", "verdict"])
        for i in range(len(note_t)):
            w.writerow([i, f"{note_t[i]:.3f}", note_kind[i], f"{gaps[i]:.3f}",
                        f"{press_t[i]:.3f}" if hit[i] else "",
                        BUTTON_NAMES.get(int(btn[i]), "") if hit[i] else "",
                        f"{err[i]:.3f}" if hit[i] else "", classify(err[i])])

    runs_csv = outdir / "runs.csv"
    with runs_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["run", "start_s", "notes", "note_gap_ms", "taps_matched",
                    "median_err_ms", "alternation", "simult_pairs",
                    "interval_cv", "buttons"])
        for ri, r in enumerate(runs):
            ids = [i for i in r if hit[i]]
            e = err[ids] if ids else np.array([])
            ts = press_t[ids] if ids else np.array([])
            iv = np.diff(ts) if len(ts) > 1 else np.array([])
            bs = [BUTTON_NAMES.get(int(btn[i]), "-") for i in r]
            alt = (sum(1 for x, y in zip(bs, bs[1:]) if x != y) / max(1, len(bs) - 1))
            w.writerow([ri, f"{note_t[r[0]]/1000:.2f}", len(r),
                        f"{np.mean(np.diff(note_t[r])):.1f}",
                        len(ids), f"{np.median(e):.1f}" if len(e) else "",
                        f"{alt:.2f}",
                        int((iv < 25).sum()) if len(iv) else 0,
                        f"{iv.std()/iv.mean():.3f}" if len(iv) and iv.mean() else "",
                        "".join(x[0] for x in bs)])

    (outdir / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    out(f"wrote {pairs_csv}")
    out(f"wrote {runs_csv}")
    for p in made:
        out(f"wrote {p}")
    out(f"wrote {outdir/'report.txt'}")


if __name__ == "__main__":
    main()
