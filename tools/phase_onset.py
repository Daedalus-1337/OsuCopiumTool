"""Does the out-of-phase alternation start at the beginning of a stream, or
appear part-way through it?

Works at the level of TAP INTERVALS rather than absolute hit error, because
absolute error is contaminated by each play's unknown clock offset (only good
to +-10-20 ms), whereas the interval between two consecutive taps inside the
same run is measured on one clock and is unaffected.

For every run of 4+ notes spaced under 110 ms:
  interval deviation  dev = (actual interval / map grid) - 1
so 0 = perfectly spaced, -0.25 = the pair landed a quarter-interval too close
together. Then:

  * mean dev by position in the run        -> is interval 1 already wrong?
  * first two intervals vs the rest        -> onset vs steady state
  * dev split by which hand fired second   -> which hand's turn is mistimed
  * odd/even position asymmetry            -> the pair-split term

Usage: python phase_onset.py [taps.csv] [folder] [star_min] [star_max]
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


def main():
    taps_csv = sys.argv[1] if len(sys.argv) > 1 else "out/taps_122.csv"
    folder = sys.argv[2] if len(sys.argv) > 2 else "sample"
    smin = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    smax = float(sys.argv[4]) if len(sys.argv) > 4 else 7.0

    meta = samplemeta.load(folder)
    keep = {f for f, m in meta.items()
            if isinstance(m["sr"], (int, float)) and smin <= m["sr"] < smax}
    print(f"{len(keep)} of {len(meta)} files are {smin:.0f}-{smax:.0f} star")

    runs = defaultdict(list)
    with open(taps_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["file"] not in keep:
                continue
            rid = int(float(r["run_id"]))
            if rid < 0:
                continue
            runs[(r["file"], rid)].append(
                (fnum(r["gap_ms"]), fnum(r["err_ms"]), int(float(r["button"])), fnum(r["z"])))

    print(f"{len(runs)} runs to look at")

    # ---- per-run interval deviations -------------------------------------
    by_pos = defaultdict(list)
    onset, steady, by_second_hand = [], [], {1: [], 2: []}
    pair_split = []
    run_records = []
    for (f, rid), seq in runs.items():
        if len(seq) < 4:
            continue
        run_gaps = [g for g, e, b, z in seq[1:]]
        grid = float(np.median(run_gaps))
        if not (30 < grid < 110):
            continue
        # only analyse *uniform* streams: if the spacing varies inside the run
        # (1/16 then 1/8, for example) a single grid value is meaningless
        if min(run_gaps) <= 0 or max(run_gaps) / min(run_gaps) > 1.10:
            continue
        devs = []
        for i in range(1, len(seq)):
            g, e, b, z = seq[i]
            gp, ep, bp, zp = seq[i - 1]
            # deviation of the tap interval from the gap between those two notes
            devs.append((i - 1, (e - ep) / g, b, bp))
        if len(devs) < 3:
            continue
        for pos, dev, b, bp in devs:
            by_pos[min(pos, 8)].append(dev)
            by_second_hand[b].append(dev)
        first2 = np.mean([d for _, d, _, _ in devs[:2]])
        rest = np.mean([d for _, d, _, _ in devs[2:]])
        onset.append(first2)
        steady.append(rest)
        ev = [d for pos, d, _, _ in devs if pos % 2 == 0]
        od = [d for pos, d, _, _ in devs if pos % 2 == 1]
        if ev and od:
            pair_split.append(np.mean(ev) - np.mean(od))
        run_records.append({"file": f, "run_id": rid, "len": len(seq), "grid": grid,
                            "onset": first2, "rest": rest,
                            "first_dev": devs[0][1],
                            "mean_dev": float(np.mean([d for _, d, _, _ in devs])),
                            "taps": len(seq)})

    onset = np.array(onset); steady = np.array(steady)
    print()
    print("=" * 88)
    print(f"INTERVAL DEVIATION INSIDE STREAMS  ({len(onset)} runs of 4+ notes, "
          f"{smin:.0f}-{smax:.0f} star)")
    print("=" * 88)
    print(f"{'position in run':<20}{'intervals':>11}{'mean dev':>11}{'median dev':>12}"
          f"{'|dev|>15%':>11}")
    for pos in sorted(by_pos):
        v = np.array(by_pos[pos])
        label = f"interval {pos+1}" + ("+" if pos == 8 else "")
        print(f"{label:<20}{len(v):>11}{100*v.mean():>10.1f}%{100*np.median(v):>11.1f}%"
              f"{100*(np.abs(v)>0.15).mean():>10.1f}%")

    print()
    print(f"  first two intervals of a run : mean dev {100*onset.mean():+6.1f}%"
          f"   median {100*np.median(onset):+6.1f}%")
    print(f"  all later intervals          : mean dev {100*steady.mean():+6.1f}%"
          f"   median {100*np.median(steady):+6.1f}%")
    d = steady - onset
    print(f"  onset minus steady state     : {100*d.mean():+6.1f}%"
          f"  (per-run sd {100*d.std():.1f}%)")
    print(f"  runs whose first interval was already off by over 15%:"
          f" {100*np.mean(np.abs([r['first_dev'] for r in run_records])>0.15):.1f}%")
    print(f"  runs already off by over 30% at the first interval   :"
          f" {100*np.mean(np.abs([r['first_dev'] for r in run_records])>0.30):.1f}%")

    print()
    print("  interval deviation by the hand that fired SECOND in the pair:")
    for b in (1, 2):
        v = np.array(by_second_hand.get(b, []))
        if len(v) > 20:
            name = {1: "pen tip (M1)", 2: "keyboard (M2)"}.get(b, str(b))
            print(f"    {name:<16} n={len(v):6d}   mean dev {100*v.mean():+6.1f}%"
                  f"   median {100*np.median(v):+6.1f}%")
    if len(by_second_hand.get(1, [])) > 20 and len(by_second_hand.get(2, [])) > 20:
        a = np.array(by_second_hand[1]); b_ = np.array(by_second_hand[2])
        print(f"    -> difference: {100*(b_.mean()-a.mean()):+.1f}% of an interval"
              f"  (positive means the second input arrives late)")

    ps = np.array(pair_split)
    print()
    print(f"  pair-split term (even-position minus odd-position deviation):"
          f" mean {100*ps.mean():+6.1f}%, sd {100*ps.std():.1f}%, "
          f"|split|>15% in {100*np.mean(np.abs(ps)>0.15):.1f}% of runs")

    # is onset/steady related to run length?
    print()
    print(f"{'run length':<12}{'runs':>7}{'onset dev':>12}{'steady dev':>13}{'split':>9}")
    for lo, hi in ((4, 5), (6, 8), (9, 12), (13, 20), (21, 99)):
        sel = [r for r in run_records if lo <= r["len"] <= hi]
        if len(sel) < 5:
            continue
        print(f"{lo}-{hi if hi<99 else '+':<9}{len(sel):>7}"
              f"{100*np.mean([r['onset'] for r in sel]):>11.1f}%"
              f"{100*np.mean([r['rest'] for r in sel]):>12.1f}%"
              f"{100*np.mean([r['mean_dev'] for r in sel]):>8.1f}%")

    # ---- plot -----------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), dpi=130)
    positions, means, ses = [], [], []
    for pos in sorted(by_pos):
        v = np.array(by_pos[pos])
        if len(v) < 20:
            continue
        positions.append(pos + 1)
        means.append(100 * v.mean())
        ses.append(100 * v.std() / np.sqrt(len(v)))
    axes[0].errorbar(positions, means, yerr=ses, fmt="o-", capsize=3, color="#d62728")
    axes[0].axhline(0, color="k", lw=0.8)
    axes[0].set_xlabel("interval number within the run")
    axes[0].set_ylabel("deviation from the map grid (%)")
    axes[0].set_title("where in a stream the spacing breaks down")
    axes[1].scatter(100 * onset, 100 * steady, s=12, alpha=0.5)
    lim = [min(axes[1].get_xlim()[0], -60), max(axes[1].get_xlim()[1], 60)]
    axes[1].plot(lim, lim, "k--", lw=0.8, label="onset = steady state")
    axes[1].axhline(0, color="grey", lw=0.6); axes[1].axvline(0, color="grey", lw=0.6)
    axes[1].set_xlabel("first two intervals (% dev)")
    axes[1].set_ylabel("rest of the run (% dev)")
    axes[1].set_title("if points sit on the line, it starts wrong and stays wrong")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig("out/plots/phase_onset.png")
    print("\nwrote out/plots/phase_onset.png")


if __name__ == "__main__":
    main()
