"""Where inside a stream does the timing go wrong?

Uses out/taps.csv (per-tap: gap, normalised error, button, run id, run length).
For runs long enough to have a shape, reports mean error and 300-rate against
position within the run - entering, middle, leaving. That distinguishes "late
into the stream" (a reaction problem) from "accelerating through it" (a rate
problem), which look identical in any whole-play average.

Also re-cuts the accuracy-vs-spacing curve inside star-rating bands, because a
pooled curve mixes maps of very different difficulty.

Usage: python run_position.py [outdir]
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def fnum(x):
    try:
        v = float(x)
        return v if not math.isnan(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def main():
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    taps = list(csv.DictReader(open(outdir / "taps.csv", encoding="utf-8")))
    man = {r["sample_file"]: r for r in csv.DictReader(
        open(outdir / "sample_manifest.csv", encoding="utf-8"))}
    print(f"{len(taps)} taps")

    runs = defaultdict(list)
    for t in taps:
        rid = (t["file"], t["run_id"])
        runs[rid].append(t)

    # ---- error by position in run ---------------------------------------
    MINLEN = 6
    positions = defaultdict(lambda: {"z": [], "err": []})
    entry_exit = []
    for (f, rid), ts in runs.items():
        if len(ts) < MINLEN:
            continue
        idx = list(range(len(ts)))
        frac = np.array(idx) / (len(ts) - 1)
        for t, fr in zip(ts, frac):
            b = min(3, int(fr * 4))          # quarters of the run
            positions[b]["z"].append(fnum(t["z"]))
            positions[b]["err"].append(fnum(t["err_ms"]))
        entry_exit.append((len(ts),
                           np.mean([fnum(t["err_ms"]) for t in ts[:3]]),
                           np.mean([fnum(t["err_ms"]) for t in ts[-3:]])))

    print()
    print("=" * 84)
    print(f"ERROR BY POSITION INSIDE RUNS OF {MINLEN}+ NOTES  (n runs = {len(entry_exit)})")
    print("=" * 84)
    print(f"{'quarter of run':<18}{'taps':>7}{'300-rate':>10}{'mean error':>13}")
    for b in sorted(positions):
        z = np.array(positions[b]["z"]); e = np.array(positions[b]["err"])
        m = np.isfinite(z)
        if not m.any():
            continue
        print(f"{['entry','second quarter','third quarter','exit'][b]:<18}"
              f"{int(m.sum()):>7}{100*(z[m]<=1).mean():>10.1f}{e[m].mean():>+13.1f}")
    if entry_exit:
        ee = np.array(entry_exit)
        print(f"\n  entry (first 3 taps) mean error : {ee[:,1].mean():+6.1f} ms")
        print(f"  exit  (last 3 taps)  mean error : {ee[:,2].mean():+6.1f} ms")
        print(f"  change across the run           : {ee[:,2].mean()-ee[:,1].mean():+6.1f} ms")
        print("  (negative change = the player drifts earlier as the run goes on, i.e. accelerates)")

    # ---- spacing curve by star band -------------------------------------
    bands = [("<=5*", 0, 5), ("5-6*", 5, 6), ("6-7*", 6, 7), ("7-8*", 7, 8), ("8+*", 8, 99)]
    BINS = [(0, 70), (70, 85), (85, 110), (110, 140), (140, 180), (180, 240),
            (240, 330), (330, 10 ** 9)]
    print()
    print("=" * 84)
    print("300-RATE vs NOTE SPACING, SPLIT BY STAR RATING")
    print("=" * 84)
    header = f"{'spacing':>14}"
    for name, _, _ in bands:
        header += f"{name:>9}"
    print(header)
    for lo, hi in BINS:
        line = f"{(f'{lo}-{hi}' if hi < 10**9 else f'{lo}+')+' ms':>14}"
        any_ = False
        for name, b0, b1 in bands:
            z = []
            for t in taps:
                sr = fnum(man.get(t["file"], {}).get("sr", "nan"))
                if not (b0 <= sr < b1):
                    continue
                g = fnum(t["gap_ms"])
                if lo <= g < hi:
                    z.append(fnum(t["z"]))
            z = np.array(z)
            z = z[np.isfinite(z)]
            if len(z) >= 30:
                line += f"{100*(z<=1).mean():>9.1f}"
                any_ = True
            else:
                line += f"{'-':>9}"
        if any_:
            print(line)

    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=130)
    for b, lab, col in ((0, "entry (first quarter)", "#1f77b4"),
                        (1, "second quarter", "#2ca02c"),
                        (2, "third quarter", "#ff7f0e"),
                        (3, "exit (last quarter)", "#d62728")):
        e = np.array(positions[b]["err"]); e = e[np.isfinite(e)]
        if len(e):
            ax.hist(e, bins=np.arange(-120, 121, 6), histtype="step", lw=1.8,
                    label=f"{lab}  n={len(e)}", color=col)
    ax.axvline(0, color="k", lw=0.8)
    ax.axvspan(-28.4, 28.4, color="green", alpha=0.08, label="300 window (OD8.6)")
    ax.set_xlabel("hit error (ms)")
    ax.set_ylabel("taps")
    ax.set_title("where inside a long run the timing goes")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = outdir / "plots" / "run_position.png"
    fig.savefig(p)
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
