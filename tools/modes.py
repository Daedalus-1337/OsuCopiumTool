"""Is the hit-error distribution smooth, or is it a comb of separate modes?

If it is a comb, the question is what generates it. Three candidates, all
testable:

  1. the two hands sit at different offsets  -> each hand is unimodal, and the
     modes coincide with each hand's median
  2. input quantisation (tablet report rate) -> modes evenly spaced, spacing
     equal to a plausible polling period
  3. rhythm locking on a subdivision        -> mode spacing a simple fraction
     of the note grid

Section A counts modes per play and measures their spacing.
Section B asks whether the hands explain them.
Section C does the same for the interval structure inside streams, per run,
which is immune to each play's unknown clock offset.

Usage: python modes.py [taps.csv] [folder] [star_min] [star_max]
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import samplemeta


def fnum(x):
    try:
        v = float(x)
        return v if not np.isnan(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def smooth(y, sigma=1.0):
    r = int(4 * sigma)
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(y, k, mode="same")


def peaks_of(hist, minsep=4, prom=0.20):
    """local maxima, smoothed, at least `minsep` bins apart, at least `prom` of
    the maximum above the intervening minimum"""
    h = smooth(hist.astype(float), 2.5)
    if h.max() <= 0:
        return []
    out = []
    for i in range(2, len(h) - 2):
        if h[i] >= h[i - 1] and h[i] > h[i + 1] and h[i] >= prom * h.max():
            if not out or i - out[-1] >= minsep:
                out.append(i)
            elif h[i] > h[out[-1]]:
                out[-1] = i
    return out


def main():
    taps_csv = sys.argv[1] if len(sys.argv) > 1 else "out/taps_122.csv"
    folder = sys.argv[2] if len(sys.argv) > 2 else "sample"
    smin = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    smax = float(sys.argv[4]) if len(sys.argv) > 4 else 7.0
    meta = samplemeta.load(folder)
    keep = {f for f, m in meta.items()
            if isinstance(m["sr"], (int, float)) and smin <= m["sr"] < smax}

    per_file = defaultdict(list)
    with open(taps_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["file"] not in keep:
                continue
            e = fnum(r["err_ms"])
            if np.isfinite(e):
                per_file[r["file"]].append((e, int(float(r["button"]))))

    print(f"{len(per_file)} replays at {smin:.0f}-{smax:.0f} star, "
          f"{sum(len(v) for v in per_file.values())} taps")

    # ---- A. modes per play ----------------------------------------------
    edges = np.arange(-70, 71, 1.0)
    centres = (edges[:-1] + edges[1:]) / 2
    nmodes = []
    nmodes_null = []
    spacings = []
    described = 0
    rows = []
    all_centred = []
    hand_split = []
    rng = np.random.default_rng(12345)
    for f, taps in per_file.items():
        if len(taps) < 150:
            continue
        e = np.array([t[0] for t in taps])
        b = np.array([t[1] for t in taps])
        med = np.median(e)
        h, _ = np.histogram(e - med, bins=edges)
        pk = peaks_of(h)
        nmodes.append(len(pk))
        # null: how many peaks would a single smooth unimodal distribution of
        # the same size and spread produce under the same detector?
        nulls = []
        for _ in range(6):
            sim = rng.normal(0.0, max(1.0, e.std()), len(e))
            hs, _ = np.histogram(sim, bins=edges)
            nulls.append(len(peaks_of(hs)))
        nmodes_null.append(float(np.mean(nulls)))
        if len(pk) >= 2:
            spacings.append(np.mean(np.diff(pk)))
        all_centred.append(e - med)
        m1 = (e - med)[b == 1]
        m2 = (e - med)[b == 2]
        if len(m1) > 30 and len(m2) > 30:
            hand_split.append((f, np.median(m1), np.median(m2), len(pk)))
        # the reported shape: a peak near +5, another near -10
        pos = [centres[i] for i in pk]
        if any(2 <= p <= 8 for p in pos) and any(-14 <= p <= -6 for p in pos):
            described += 1
        rows.append((f, len(pk), len(e), med))

    print()
    print("=" * 88)
    print("A. HOW MANY SEPARATE PEAKS DOES A SINGLE PLAY'S ERROR DISTRIBUTION HAVE?")
    print("=" * 88)
    nmodes = np.array(nmodes)
    null = np.array(nmodes_null)
    print(f"  plays examined                     : {len(nmodes)}")
    print(f"  observed peaks per play            : median {np.median(nmodes):.0f}"
          f"   range {nmodes.min()}-{nmodes.max()}")
    print(f"  peaks a smooth unimodal distribution would give: median {np.median(null):.1f}")
    print(f"  plays with more peaks than the null predicts by 2 or more:"
          f" {int((nmodes > null + 2).sum())} of {len(nmodes)}"
          f" ({100*(nmodes > null + 2).mean():.0f}%)")
    print(f"  distribution of observed counts:")
    for k in range(1, 9):
        n = int((nmodes == k).sum())
        if n:
            print(f"    {k} peak{'s' if k > 1 else ' '}: {n:3d} plays")
    print(f"    {'9+' if (nmodes >= 9).any() else '':>7}: {int((nmodes >= 9).sum()):3d} plays")
    if spacings:
        sp = np.array(spacings)
        print(f"  spacing between adjacent peaks     : median {np.median(sp):.1f} ms"
              f"   p25 {np.percentile(sp,25):.1f}   p75 {np.percentile(sp,75):.1f}")
        for lo, hi, name in ((3.5, 4.5, "4 ms  = 250 Hz polling"),
                             (4.5, 5.5, "5 ms  = 200 Hz polling"),
                             (7.0, 8.0, "7.5 ms = 133 Hz polling"),
                             (8.0, 9.0, "8 ms  = 125 Hz polling")):
            n = int(((sp >= lo) & (sp < hi)).sum())
            if n:
                print(f"    {n:2d} plays with peaks about {name}")
    print(f"  plays with a peak near +5 AND another near -10 (your description):"
          f" {described} of {len(nmodes)}")

    # ---- B. do the hands explain the modes? -----------------------------
    print()
    print("=" * 88)
    print("B. IF THE COMB IS THE TWO HANDS, EACH HAND SHOULD SIT AT ONE MODE")
    print("=" * 88)
    if hand_split:
        d = np.array([m2 - m1 for _, m1, m2, _ in hand_split])
        print(f"  plays with both hands present: {len(d)}")
        print(f"  per-play (keyboard median - pen median): median {np.median(d):+.1f} ms"
              f"   p25 {np.percentile(d,25):+.1f}   p75 {np.percentile(d,75):+.1f}")
        print(f"  plays with more than 2 peaks but the hands only {np.abs(d).__gt__(8).sum()}"
              f" sit more than 8 ms apart -> the extra peaks are NOT the hands")

    # ---- C. interval structure per run ---------------------------------
    runs = defaultdict(list)
    with open(taps_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["file"] not in keep:
                continue
            rid = int(float(r["run_id"]))
            if rid >= 0:
                runs[(r["file"], rid)].append(
                    (fnum(r["gap_ms"]), fnum(r["err_ms"]), int(float(r["button"]))))
    head_to_head = []
    for (f, rid), seq in runs.items():
        if len(seq) < 6:
            continue
        run_gaps = [g for g, e, b in seq[1:]]
        grid = float(np.median(run_gaps))
        if not (30 < grid < 110):
            continue
        if min(run_gaps) <= 0 or max(run_gaps) / min(run_gaps) > 1.10:
            continue
        d1, d2 = [], []
        for i in range(1, len(seq)):
            g, e, b = seq[i]
            gp, ep, bp = seq[i - 1]
            dev = (e - ep) / g
            (d2 if b == 2 else d1).append(dev)
        if len(d1) >= 2 and len(d2) >= 2:
            head_to_head.append(np.mean(d2) - np.mean(d1))
    print()
    print("=" * 88)
    print("C. WITHIN A STREAM, IS ONE HAND'S TURN SYSTEMATICALLY EARLY?  (per run)")
    print("=" * 88)
    if head_to_head:
        h = np.array(head_to_head) * 100
        print(f"  runs with both hands alternating: {len(h)}")
        print(f"  keyboard-turn interval minus pen-turn interval, per run:")
        print(f"    median {np.median(h):+6.1f}% of the grid"
              f"   p25 {np.percentile(h,25):+6.1f}%   p75 {np.percentile(h,75):+6.1f}%")
        print(f"    runs where the keyboard turn is more than 10% short:"
              f" {100*np.mean(h < -10):.1f}%")
        print(f"    runs where the pen turn is more than 10% short     :"
              f" {100*np.mean(h > 10):.1f}%")
        print(f"    runs where neither is off by 10%                   :"
              f" {100*np.mean(np.abs(h) <= 10):.1f}%")

    # ---- pooled plot ----------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), dpi=130)
    if all_centred:
        allc = np.concatenate(all_centred)
        axes[0].hist(allc, bins=np.arange(-60, 61, 1), histtype="step", lw=1.4,
                     color="#d62728", label="all taps, each play centred on its own median")
        axes[0].set_xlabel("hit error minus that play's median (ms)")
        axes[0].set_ylabel("taps")
        axes[0].set_title("pooled, within-play: is there a comb?")
        axes[0].legend(fontsize=8)
    if spacings:
        axes[1].hist(spacings, bins=np.arange(1, 16, 0.5), color="#1f77b4")
        for x, lab in ((4, "4 ms"), (5, "5 ms"), (7.5, "7.5 ms"), (8, "8 ms")):
            axes[1].axvline(x, color="k", ls=":", lw=0.8)
            axes[1].text(x, axes[1].get_ylim()[1] * 0.9, lab, fontsize=7, rotation=90,
                         ha="right")
        axes[1].set_xlabel("spacing between peaks in one play (ms)")
        axes[1].set_ylabel("plays")
        axes[1].set_title("mode spacing vs plausible input polling periods")
    fig.tight_layout()
    fig.savefig("out/plots/modes.png")
    print("\nwrote out/plots/modes.png")


if __name__ == "__main__":
    main()
