"""Per-player metrics from a batch.py output folder, under one fixed definition.

Every quantity is computed the same way for every player. Nothing here knows
which group a player is in, or what the expected direction of any difference is.

    python exp_metrics.py <batch_dir> <label> <outdir>

<batch_dir> is a folder written by batch.py: it holds taps.csv (one row per
matched tap: file, gap_ms, z, button, run_len, run_id, err_ms) and batch.csv
(one row per play, which carries the play's own 300 hit window, w300).

Writes <outdir>/metrics_<label>.csv  (one row per play)
  and  <outdir>/runs_<label>.csv     (one row per stream run)
and prints a compact summary.

Definitions
-----------
A *stream run* is a maximal sequence of consecutive tapped notes whose note
spacing is below FAST. It is reconstructed here from gap_ms; the run_id column
of taps.csv is not used because analyze.py builds that one with a 1e9 threshold,
which groups the entire map.

The press interval between consecutive notes i-1 and i is

    press_i - press_(i-1) = gap_i + err_i - err_(i-1)

so the interval error in milliseconds, needing no absolute clock, is

    delta_i = err_i - err_(i-1)

and its deviation from the grid is delta_i / gap_i. The accuracy window is each
play's own w300, never a fixed constant, so plays of different OD are comparable.

For a strictly alternating, evenly spaced pair of intervals,

    alt = (delta_i - delta_(i+1)) / 2      alternating (hand-to-hand) part
    com = (delta_i + delta_(i+1)) / 2      shared (both-hands) part

Under independent per-tap jitter the alternating part carries three times the
variance of the shared part, so

    phase_ms = sqrt(max(0, rms(alt)^2 - 3 rms(com)^2))    hand-to-hand separation
    noise_ms = rms(com) * sqrt(2)                         per-tap jitter floor

phase_ms is the rms disagreement between the two inputs inside a stream with
each input's own jitter removed; noise_ms is the jitter one tap has on its own.
Both are milliseconds. Their ratio is dimensionless, so it compares two players
without either one's overall precision setting the scale.

Bands, thresholds and the alternation requirement are fixed here; changing them
changes both groups equally.
"""
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

FAST = 110.0           # ms; note spacing at or above this is not a stream note
NEAR_SIMULT = 25.0     # ms; a press interval this short means a pair was re-pressed
BANDS = [(0, 70), (70, 85), (85, 110), (110, 130), (130, 160),
         (160, 200), (200, 300), (300, 1e9)]
NBOOT = 2000
RNG = np.random.default_rng(20261101)


def band_label(lo, hi):
    return f"{lo:.0f}-{hi:.0f}" if hi < 1e8 else f"{lo:.0f}+"


def load(batch_dir):
    """-> (plays, windows) where plays is {play: [(gap, btn, err), ...]} in the
    order the taps were written (note order), and windows is {play: w300}."""
    plays = defaultdict(list)
    with (Path(batch_dir) / "taps.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                gap = float(r["gap_ms"]); err = float(r["err_ms"])
                btn = int(float(r["button"]))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(gap) or not math.isfinite(err) or gap <= 0:
                continue
            plays[r["file"]].append((gap, btn, err))
    windows = {}
    bpath = Path(batch_dir) / "batch.csv"
    if bpath.exists():
        with bpath.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    windows[r["file"]] = float(r["w300"])
                except (TypeError, ValueError, KeyError):
                    pass
    return plays, windows


def stream_runs(taps):
    """Maximal runs of consecutive taps spaced below FAST."""
    runs, cur = [], []
    for t in taps:
        if t[0] < FAST:
            cur.append(t)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def run_intervals(run):
    """[(delta, gap, btn_prev, btn_next)] for a run's consecutive taps."""
    return [(run[i][2] - run[i - 1][2], run[i][0], run[i - 1][1], run[i][1])
            for i in range(1, len(run))]


def alt_pairs(iv):
    """Strictly alternating, evenly spaced consecutive interval pairs.

    Intervals are (delta, gap, btn_prev, btn_next). Two consecutive intervals
    form an alternating triple when interval i alternates (btn_prev != btn_next)
    and interval i+1 does too (btn_next != b2)."""
    out = []
    for i in range(len(iv) - 1):
        d0, g0, bp, bn = iv[i]
        d1, g1, bn_check, b2 = iv[i + 1]
        if bp == bn or bn == b2:
            continue
        if abs(g0 - g1) > 0.15 * g0:
            continue
        out.append((d0, d1, g0 if g0 < g1 else g1))
    return out


def phase_noise(pairs, min_pairs=8):
    if len(pairs) < min_pairs:
        return None
    d0 = np.array([p[0] for p in pairs])
    d1 = np.array([p[1] for p in pairs])
    ra = float(np.sqrt(np.mean(((d0 - d1) / 2.0) ** 2)))
    rc = float(np.sqrt(np.mean(((d0 + d1) / 2.0) ** 2)))
    return ra, rc, math.sqrt(max(0.0, ra * ra - 3 * rc * rc)), rc * math.sqrt(2)


def play_metrics(taps, w300):
    runs = stream_runs(taps)
    if not runs:
        return None
    ivs = [run_intervals(r) for r in runs]
    alliv = [x for iv in ivs for x in iv]
    pairs = [p for iv in ivs for p in alt_pairs(iv)]
    if not alliv:
        return None

    gaps = np.array([t[0] for t in taps])
    errs = np.array([t[2] for t in taps])
    btns = np.array([t[1] for t in taps])
    fast = gaps < FAST
    slow = ~fast
    n300 = np.abs(errs) <= (w300 if w300 else 28.4)

    m = {"n_taps": len(taps), "n_stream_notes": int(fast.sum()),
         "n_slow_notes": int(slow.sum()), "n_runs": len(runs),
         "n_interval": len(alliv), "n_alt_pairs": len(pairs),
         "w300": w300 if w300 else float("nan")}
    m["grid_ms"] = float(np.median([x[1] for x in alliv]))
    m["rate300_slow"] = 100 * float(n300[slow].mean()) if slow.any() else float("nan")
    m["rate300_stream"] = 100 * float(n300[fast].mean()) if fast.any() else float("nan")
    m["stream_penalty_pp"] = m["rate300_slow"] - m["rate300_stream"]
    m["input2_share_slow"] = 100 * float((btns[slow] == 2).mean()) if slow.any() else float("nan")
    m["input2_share_stream"] = 100 * float((btns[fast] == 2).mean()) if fast.any() else float("nan")

    delta = np.array([x[0] for x in alliv])
    dgap = np.array([x[1] for x in alliv])
    dev = delta / dgap
    m["on_grid15"] = 100 * float((np.abs(dev) <= 0.15).mean())
    m["off_grid50"] = 100 * float((np.abs(dev) > 0.50).mean())
    m["interval_rms_ms"] = float(np.sqrt(np.mean(delta ** 2)))
    m["near_simult"] = 100 * float((np.abs(dgap + delta) < NEAR_SIMULT).mean())
    same = sum(1 for x in alliv if x[2] == x[3])
    m["alt_rate"] = 100 * (1 - same / len(alliv))

    firsts = [(r[1][2] - r[0][2], r[1][0]) for r in runs
              if len(r) >= 3 and r[0][1] != r[1][1]]
    if firsts:
        f = np.array([x[0] for x in firsts]); fg = np.array([x[1] for x in firsts])
        m["n_entry"] = len(firsts)
        m["entry_ms"] = float(np.median(f))
        m["entry_dev_pct"] = 100 * float(np.median(f / fg))
        m["entry_off15"] = 100 * float((np.abs(f / fg) > 0.15).mean())
        m["entry_off30"] = 100 * float((np.abs(f / fg) > 0.30).mean())
    pn = phase_noise(pairs)
    if pn:
        m["rms_alt_ms"], m["rms_com_ms"], m["phase_ms"], m["noise_ms"] = pn
        m["phase_over_noise"] = pn[2] / pn[3] if pn[3] else float("nan")
    return m


def boot_ci(per_play, key, weight_key="n_taps"):
    vals = [(p[key], p.get(weight_key, 1)) for p in per_play
            if key in p and p[key] == p[key]]
    if len(vals) < 2:
        return float("nan"), float("nan")
    v = np.array([x[0] for x in vals]); w = np.array([x[1] for x in vals], float)
    w = np.where(np.isfinite(w) & (w > 0), w, 1.0)
    idx = np.arange(len(v))
    out = np.empty(NBOOT)
    for b in range(NBOOT):
        pick = RNG.choice(idx, size=len(idx), replace=True)
        ww = w[pick]
        out[b] = float((v[pick] * ww).sum() / ww.sum()) if ww.sum() else np.nan
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def ratio_ci(per_play, num, den):
    """Cluster bootstrap CI for a ratio of pooled means."""
    rows = [(p[num], p[den], p.get("n_taps", 1)) for p in per_play
            if p.get(num) == p.get(num) and p.get(den) == p.get(den)
            and p.get(num) is not None and p.get(den) is not None]
    if len(rows) < 2:
        return float("nan"), float("nan")
    a = np.array([r[0] for r in rows]); b = np.array([r[1] for r in rows])
    w = np.array([r[2] for r in rows], float)
    w = np.where(np.isfinite(w) & (w > 0), w, 1.0)
    idx = np.arange(len(a)); out = np.empty(NBOOT)
    for k in range(NBOOT):
        pick = RNG.choice(idx, size=len(idx), replace=True)
        ww = w[pick]
        out[k] = (a[pick] * ww).sum() / (b[pick] * ww).sum() if (b[pick] * ww).sum() else np.nan
    return float(np.nanpercentile(out, 2.5)), float(np.nanpercentile(out, 97.5))


def main():
    batch_dir, label, outdir = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    outdir.mkdir(parents=True, exist_ok=True)
    plays, windows = load(batch_dir)

    per_play, run_rows, all_taps = [], [], []
    for pf, taps in sorted(plays.items()):
        w = windows.get(pf)
        m = play_metrics(taps, w)
        all_taps.extend(taps)
        if m is None or m["n_taps"] < 30:
            continue
        m["file"] = pf
        per_play.append(m)
        for run in stream_runs(taps):
            pn = phase_noise(alt_pairs(run_intervals(run)))
            if not pn:
                continue
            run_rows.append({"file": pf, "notes": len(run),
                             "grid_ms": float(np.median([t[0] for t in run[1:]])),
                             "phase_ms": pn[2], "noise_ms": pn[3]})

    # union of keys in first-seen order: optional fields (entry, phase) appear
    # only on plays that have them, so the first row is not a safe field list
    keys = []
    for p in per_play:
        for k in p:
            if k not in keys:
                keys.append(k)
    with (outdir / f"metrics_{label}.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(per_play)
    with (outdir / f"runs_{label}.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=["file", "notes", "grid_ms", "phase_ms", "noise_ms"])
        wr.writeheader(); wr.writerows(run_rows)

    print(f"=== {label}: {len(per_play)} plays with stream content, "
          f"{len(all_taps)} taps, {len(run_rows)} phase-analysable runs ===")
    print(f"median grid {np.median([p['grid_ms'] for p in per_play]):.0f} ms   "
          f"median stream notes/play {int(np.median([p['n_stream_notes'] for p in per_play]))}   "
          f"median slow notes/play {int(np.median([p['n_slow_notes'] for p in per_play]))}")
    print()
    # pooled band table (per tap, 300 window per play)
    wmap = {}
    for pf, taps in plays.items():
        wmap[pf] = windows.get(pf) or 28.4
    band = {band_label(lo, hi): [0, 0, 0] for lo, hi in BANDS}
    for pf, taps in plays.items():
        for gap, btn, err in taps:
            for lo, hi in BANDS:
                if lo <= gap < hi:
                    k = band_label(lo, hi)
                    band[k][0] += 1; band[k][1] += int(abs(err) <= wmap[pf])
                    band[k][2] += int(btn == 2)
                    break
    print("ACCURACY BY ABSOLUTE SPACING BAND (pooled taps, each play's own window)")
    print(f"  {'band ms':<9}{'taps':>8}{'300-rate':>10}{'input-2 share':>15}")
    for lo, hi in BANDS:
        k = band_label(lo, hi); n, n3, nk = band[k]
        if n:
            print(f"  {k:<9}{n:>8}{100*n3/n:>9.1f}%{100*nk/n:>14.1f}%")
    print()

    def show(key, fmt="{:.1f}"):
        lo, hi = boot_ci(per_play, key)
        vals = [p[key] for p in per_play if key in p and p[key] == p[key]]
        print(f"  {key:<22} median {fmt.format(float(np.median(vals))):>8}   "
              f"pooled-mean {fmt.format(float(np.mean(vals))):>8}   "
              f"95% CI [{fmt.format(lo)}, {fmt.format(hi)}]   n={len(vals)}")

    print("SLOW vs STREAM, AND THE STREAM PENALTY")
    show("rate300_slow"); show("rate300_stream"); show("stream_penalty_pp", "{:+.1f}")
    print()
    print("INPUT FINGERPRINT (share of taps on the second input)")
    show("input2_share_slow"); show("input2_share_stream")
    print()
    print("IN-RUN INTERVAL STRUCTURE")
    show("n_runs", "{:.0f}"); show("on_grid15"); show("off_grid50")
    show("interval_rms_ms"); show("near_simult"); show("alt_rate")
    print()
    print("ENTRY (first interval of each alternating run)")
    show("entry_ms", "{:+.1f}"); show("entry_dev_pct", "{:+.1f}")
    show("entry_off15"); show("entry_off30")
    print()
    print("PHASE vs JITTER (ms, from alternating interval pairs)")
    show("rms_alt_ms"); show("rms_com_ms"); show("phase_ms"); show("noise_ms")
    lo, hi = ratio_ci(per_play, "phase_ms", "noise_ms")
    vals = [p["phase_over_noise"] for p in per_play if "phase_over_noise" in p]
    print(f"  {'phase_over_noise':<22} median {float(np.median(vals)):>8.3f}   "
          f"95% CI [{lo:.3f}, {hi:.3f}]   n={len(vals)}")
    print()
    print(f"wrote {outdir}/metrics_{label}.csv and runs_{label}.csv")


if __name__ == "__main__":
    main()
