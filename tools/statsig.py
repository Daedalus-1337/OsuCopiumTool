"""How many replays are needed, and are "accuracy" and "stream accuracy"
actually two separate problems?

Reads out/batch.csv (one row per replay), then:

 A. reports, per metric, the between-play spread and how many plays are needed
    to pin that metric down to a given precision
 B. separates each play's error on SLOW notes into a centring part (bias) and a
    scatter part (jitter) - if the non-stream error is mostly centring then it
    is the same failure as the stream error, just milder; if it is mostly
    scatter then it really is a separate problem
 C. reports the paired within-play contrasts (fast vs slow, keyboard vs pen),
    which cancel day-to-day variance and so need far fewer plays
"""
from __future__ import annotations

import csv
import math

import numpy as np

Z = 1.959964          # 95%


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def load(path="out/batch.csv"):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        def f(k):
            v = r.get(k, "")
            try:
                v = float(v)
            except ValueError:
                return float("nan")
            return v if not math.isnan(v) else float("nan")
        r["_"] = {k: f(k) for k in ("bias", "sd", "alt_rate", "sparse_rate",
                                   "alt_bias", "alt_sd", "sparse_bias", "sparse_sd",
                                   "alt_n", "sparse_n", "interhand_offset",
                                   "ongrid_pct", "alternation_pct", "w300",
                                   "rate300_all", "alt_rate300_b1", "alt_rate300_b2",
                                   "alt_n_b1", "alt_n_b2", "alt_bias_b1", "alt_bias_b2",
                                   "gain_bias_removed", "acc_reported", "clock_delta")}
        rows.append(r)
    return rows


def needed(sd, half, z=Z):
    if not np.isfinite(sd) or sd <= 0 or half <= 0:
        return float("nan")
    return (z * sd / half) ** 2


def main():
    rows = load()
    print(f"{len(rows)} replays\n")

    # ---- A. precision vs number of plays --------------------------------
    print("=" * 96)
    print("A. HOW MANY PLAYS TO PIN A NUMBER DOWN")
    print("=" * 96)
    print("   (between-play sd = how much the metric moves from play to play;")
    print("    a metric with a large between-play sd is really 'session' noise, not you)\n")

    metrics = [
        ("error on slow notes, centring (ms)",
         [r["_"]["sparse_bias"] for r in rows]),
        ("error on slow notes, scatter (ms)",
         [r["_"]["sparse_sd"] for r in rows]),
        ("300-rate on slow notes (%)",
         [r["_"]["sparse_rate"] for r in rows]),
        ("300-rate on fast notes (%) - all plays",
         [r["_"]["alt_rate"] for r in rows if np.isfinite(r["_"]["alt_rate"])]),
        ("300-rate on fast notes (%) - >=30 fast notes",
         [r["_"]["alt_rate"] for r in rows if r["_"]["alt_n"] >= 30]),
        ("in-run intervals on the grid (%)",
         [r["_"]["ongrid_pct"] for r in rows if np.isfinite(r["_"]["ongrid_pct"])]),
        ("alternation rate in runs (%)",
         [r["_"]["alternation_pct"] for r in rows if np.isfinite(r["_"]["alternation_pct"])]),
        ("inter-hand offset in runs (ms)",
         [r["_"]["interhand_offset"] for r in rows if np.isfinite(r["_"]["interhand_offset"])]),
    ]
    print(f"{'metric':<38}{'n':>4}{'mean':>8}{'sd':>8}"
          f"{'plays for +-2':>14}{'+-5':>7}{'+-10':>7}")
    for name, vals in metrics:
        v = np.array([x for x in vals if np.isfinite(x)])
        if len(v) < 3:
            print(f"{name:<38}{len(v):>4}   too few plays to say")
            continue
        sd = v.std(ddof=1)
        print(f"{name:<38}{len(v):>4}{v.mean():>8.1f}{sd:>8.1f}"
              f"{needed(sd, 2):>14.0f}{needed(sd, 5):>7.0f}{needed(sd, 10):>7.0f}")

    # ---- B. is non-stream accuracy the same failure or a different one? --
    print()
    print("=" * 96)
    print("B. YOUR HYPOTHESIS: 'accuracy and stream accuracy are two separate issues'")
    print("=" * 96)
    nostream = [r for r in rows if not np.isfinite(r["_"]["alt_rate"])
                or r["_"]["alt_n"] == 0]
    if nostream:
        print(f"   plays containing no notes too fast to single-tap ({len(nostream)}):")
        for r in nostream:
            print(f"     {r['date']}  {r['map'][:30]:<31} acc {r['_']['acc_reported']:5.2f}%"
                  f"   error on those notes: bias {r['_']['sparse_bias']:+5.1f}"
                  f"  sd {r['_']['sparse_sd']:5.1f}  300-rate {r['_']['sparse_rate']:5.1f}%")
    print()
    print("   Every play's error on notes slower than 110 ms, split into centring and scatter:")
    print(f"   {'date':<11}{'map':<26}{'n':>6}{'bias':>7}{'sd':>6}{'300%':>7}"
          f"{'if centred':>11}{'if tighter':>11}")
    for r in sorted(rows, key=lambda x: x["date"]):
        d = r["_"]
        if not np.isfinite(d["sparse_sd"]) or d["sparse_n"] < 30:
            continue
        w = d["w300"]
        centred = 100 * (2 * phi(w / d["sparse_sd"]) - 1)          # same scatter, no bias
        asis = d["sparse_rate"]
        # what scatter would give the same 300-rate as the fast notes, no bias
        target = d["alt_rate"] if np.isfinite(d["alt_rate"]) else float("nan")
        print(f"   {r['date']:<11}{r['map'][:25]:<26}{d['sparse_n']:>6.0f}{d['sparse_bias']:>+7.1f}"
              f"{d['sparse_sd']:>6.1f}{asis:>7.1f}{centred:>11.1f}")
    sb = np.array([r["_"]["sparse_bias"] for r in rows if r["_"]["sparse_n"] >= 30])
    ss = np.array([r["_"]["sparse_sd"] for r in rows if r["_"]["sparse_n"] >= 30])
    sr = np.array([r["_"]["sparse_rate"] for r in rows if r["_"]["sparse_n"] >= 30])
    print()
    print(f"   across those plays, on slow notes: |bias| median {np.median(np.abs(sb)):.1f} ms,"
          f" scatter median {np.median(ss):.1f} ms")
    # how much of the slow-note shortfall from perfect (100%) is centring vs scatter?
    print(f"   slow-note 300-rate: mean {sr.mean():.1f}%"
          f"   what it would be with the same scatter but zero bias:"
          f" {np.mean([100*(2*phi(r['_']['w300']/r['_']['sparse_sd'])-1) for r in rows if r['_']['sparse_n']>=30]):.1f}%")

    # ---- C. paired contrasts --------------------------------------------
    print()
    print("=" * 96)
    print("C. PAIRED CONTRASTS (these cancel day-to-day variance, so they need fewer plays)")
    print("=" * 96)
    pairs = [
        ("fast notes vs slow notes, 300-rate (points)",
         [r["_"]["alt_rate"] - r["_"]["sparse_rate"] for r in rows
          if np.isfinite(r["_"]["alt_rate"]) and r["_"]["alt_n"] >= 30]),
        ("fast notes vs slow notes, centring (ms)",
         [r["_"]["alt_bias"] - r["_"]["sparse_bias"] for r in rows
          if np.isfinite(r["_"]["alt_bias"]) and r["_"]["alt_n"] >= 30]),
        ("fast notes vs slow notes, scatter (ms)",
         [r["_"]["alt_sd"] - r["_"]["sparse_sd"] for r in rows
          if np.isfinite(r["_"]["alt_sd"]) and r["_"]["alt_n"] >= 30]),
        ("keyboard vs pen, 300-rate, on fast notes (points)",
         [r["_"]["alt_rate300_b2"] - r["_"]["alt_rate300_b1"] for r in rows
          if r["_"]["alt_n_b2"] >= 30 and r["_"]["alt_n_b1"] >= 30]),
        ("keyboard vs pen, centring, on fast notes (ms)",
         [r["_"]["alt_bias_b2"] - r["_"]["alt_bias_b1"] for r in rows
          if r["_"]["alt_n_b2"] >= 30 and r["_"]["alt_n_b1"] >= 30]),
    ]
    print(f"{'paired difference':<52}{'n':>4}{'mean':>8}{'sd':>7}{'t':>7}{'p<0.05?':>9}{'n for +-2':>10}")
    for name, vals in pairs:
        v = np.array([x for x in vals if np.isfinite(x)])
        if len(v) < 3:
            print(f"{name:<52}{len(v):>4}   too few")
            continue
        sd = v.std(ddof=1)
        se = sd / math.sqrt(len(v))
        t = v.mean() / se if se else float("nan")
        print(f"{name:<52}{len(v):>4}{v.mean():>+8.1f}{sd:>7.1f}{t:>7.2f}"
              f"{('yes' if abs(t) > 2.1 else 'no'):>9}{needed(sd, 2):>10.0f}")
    print()
    print("   'n for +-2' = plays needed for a 95% interval of +-2 units on the mean")
    print("   of that paired difference.")


if __name__ == "__main__":
    main()
