"""Report on the random PROFILE sample and the PROGRESSION set.

Reads out/batch.csv (per-replay metrics), out/taps.csv (per-tap, for the
accuracy-vs-spacing curve) and out/sample_manifest.csv (which replay belongs to
which set).

Usage: python report_sample.py [outdir]
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ALT_MS = 110.0
BINS = [(0, 60), (60, 70), (70, 80), (80, 95), (95, 110), (110, 130), (130, 160),
        (160, 200), (200, 260), (260, 330), (330, 450), (450, 10 ** 9)]


def fnum(x):
    try:
        v = float(x)
        return v if not math.isnan(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def load(outdir: Path):
    batch = list(csv.DictReader(open(outdir / "batch.csv", encoding="utf-8")))
    manifest = {}
    mp = outdir / "sample_manifest.csv"
    if mp.exists():
        for r in csv.DictReader(open(mp, encoding="utf-8")):
            manifest[r["sample_file"]] = r
    for r in batch:
        m = manifest.get(r["file"], {})
        r["_set"] = m.get("set", "")
        r["_sr"] = fnum(m.get("sr", "nan"))
        r["_hash"] = m.get("beatmap_hash", "")
        try:
            r["_d"] = datetime.strptime(m.get("played_at", ""), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            r["_d"] = None
    return batch, manifest


def main():
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    batch, manifest = load(outdir)
    prof = [r for r in batch if "profile" in r["_set"]]
    prog = [r for r in batch if "progression" in r["_set"]]
    print(f"{len(batch)} replays analysed:   profile {len(prof)}   progression {len(prog)}")
    L = []

    def out(s=""):
        print(s); L.append(s)

    # ---------------- PROFILE ----------------
    P = prof or batch
    w = np.array([fnum(r["w300"]) for r in P])
    E = np.concatenate([np.zeros(1)])  # placeholder
    bias = np.array([fnum(r["bias"]) for r in P])
    sd = np.array([fnum(r["sd"]) for r in P])
    r300 = np.array([fnum(r["rate300_all"]) for r in P])
    acc = np.array([fnum(r["acc_reported"]) for r in P])
    sparse_sd = np.array([fnum(r["sparse_sd"]) for r in P])
    alt_rate = np.array([fnum(r["alt_rate"]) for r in P])
    sparse_rate = np.array([fnum(r["sparse_rate"]) for r in P])
    ongrid = np.array([fnum(r["ongrid_pct"]) for r in P])
    ih = np.array([fnum(r["interhand_offset"]) for r in P])

    out()
    out("=" * 100)
    out(f"PROFILE SET - {len(P)} randomly chosen replays (all times wall-clock ms)")
    out("=" * 100)
    out(f"  recorded accuracy      : mean {np.nanmean(acc):.2f}%"
        f"   median {np.nanmedian(acc):.2f}%   range {np.nanmin(acc):.2f}-{np.nanmax(acc):.2f}%")
    out(f"  implemented accuracy   : mean {np.nanmean(r300):.2f}% of taps inside the 300 window")
    out(f"  timing bias            : mean {np.nanmean(bias):+.1f}  sd across plays {np.nanstd(bias):.1f} ms")
    out(f"  timing scatter         : mean {np.nanmean(sd):.1f} ms  (sd across plays {np.nanstd(sd):.1f})")
    mods = defaultdict(int)
    for r in P:
        m = r["mods"]
        key = ("DT" if ("DT" in m or "NC" in m) else "HR" if "HR" in m
               else "HT" if "HT" in m else "NM")
        mods[key] += 1
    out(f"  mods in the sample     : {dict(mods)}")
    srs = np.array([r["_sr"] for r in P if np.isfinite(r["_sr"])])
    if len(srs):
        out(f"  star range             : {srs.min():.2f} - {srs.max():.2f}"
            f"   median {np.median(srs):.2f}")

    out()
    out("  FAST NOTES vs THE REST (pooled over the sample)")
    m1 = fnum
    n_fast = int(np.nansum([fnum(r["alt_n"]) for r in P]))
    n_slow = int(np.nansum([fnum(r["sparse_n"]) for r in P]))
    wsum_f = n_fast if n_fast else 1
    wsum_s = n_slow if n_slow else 1
    fast300 = np.nansum([fnum(r["alt_rate"]) * fnum(r["alt_n"]) for r in P]) / wsum_f
    slow300 = np.nansum([fnum(r["sparse_rate"]) * fnum(r["sparse_n"]) for r in P]) / wsum_s
    out(f"    notes faster than {ALT_MS:.0f} ms : {fast300:5.1f}%  ({n_fast} taps)")
    out(f"    everything else        : {slow300:5.1f}%  ({n_slow} taps)")
    out(f"    shortfall              : {slow300-fast300:5.1f} points")

    out()
    out("  BY STREAM LENGTH (how long the unbroken fast run was)")
    for key, label in (("run2_2", "2 notes"), ("run3_5", "3-5"),
                       ("run6_10", "6-10"), ("run11_plus", "11+")):
        n = np.nansum([fnum(r.get(key + "_n", "nan")) for r in P])
        if not n:
            continue
        rate = np.nansum([fnum(r.get(key + "_rate", "nan")) * fnum(r.get(key + "_n", "nan"))
                          for r in P]) / n
        b = np.nansum([fnum(r.get(key + "_bias", "nan")) * fnum(r.get(key + "_n", "nan"))
                       for r in P]) / n
        out(f"    runs of {label:<7}  {int(n):6d} taps   300-rate {rate:5.1f}%"
            f"   mean error {b:+6.1f} ms")

    out()
    out("  ALTERNATION AND PHASE (runs that require alternating)")
    for label, arr, signed in (("in-run intervals within 15% of grid (%)", ongrid, False),
                               ("inter-hand offset (ms)", ih, True)):
        v = arr[np.isfinite(arr)]
        if len(v):
            fmt = "{:+6.1f}" if signed else "{:6.1f}"
            out(f"    {label:<38} mean {fmt.format(np.mean(v))}"
                f"   sd across plays {np.std(v):5.1f}   n={len(v)}")
    sims = np.array([fnum(r["simult_pct"]) for r in P])
    sims = sims[np.isfinite(sims)]
    if len(sims):
        out(f"    {'near-simultaneous tap pairs (%)':<38} mean {np.mean(sims):6.1f}")
    alts = np.array([fnum(r["alternation_pct"]) for r in P])
    alts = alts[np.isfinite(alts)]
    if len(alts):
        out(f"    {'alternation rate (%)':<38} mean {np.mean(alts):6.1f}")

    out()
    out("  PER PLAY (sorted by star rating)")
    out(f"    {'date':<11}{'sr':>5}{'mods':<6}{'acc':>7}{'300%':>7}{'bias':>7}{'sd':>7}"
        f"{'fast300':>9}{'ongrid':>8}  map")
    for r in sorted(P, key=lambda x: (x["_sr"] if np.isfinite(x["_sr"]) else 99)):
        out(f"    {(r['_d'].strftime('%Y-%m-%d') if r['_d'] else r['date']):<11}"
            f"{r['_sr']:>5.1f}{r['mods']:<6}{fnum(r['acc_reported']):>7.2f}"
            f"{fnum(r['rate300_all']):>7.1f}{fnum(r['bias']):>+7.1f}{fnum(r['sd']):>7.1f}"
            f"{fnum(r['alt_rate']):>9.1f}{fnum(r['ongrid_pct']):>8.1f}"
            f"  {r['map'][:24]} [{r['version'][:10]}]")

    # ---------------- accuracy vs spacing from taps.csv ----------------
    taps_p = outdir / "taps.csv"
    if taps_p.exists():
        gaps, zs = [], []
        with taps_p.open(encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                if row["file"] not in {r["file"] for r in P}:
                    continue
                g = fnum(row["gap_ms"]); z = fnum(row["z"])
                if np.isfinite(g) and np.isfinite(z):
                    gaps.append(g); zs.append(z)
        gaps = np.array(gaps); zs = np.array(zs)
        out()
        out("  ACCURACY AGAINST NOTE SPACING (profile sample)")
        out(f"    {'spacing':>16}{'taps':>8}{'300-rate':>10}{'95% ci':>8}")
        xs, ys, es = [], [], []
        for lo, hi in BINS:
            m = (gaps >= lo) & (gaps < hi)
            if m.sum() < 30:
                continue
            p = (zs[m] <= 1).mean()
            se = 100 * math.sqrt(p * (1 - p) / m.sum()) * 1.96
            lab = f"{lo:.0f}-{hi:.0f}" if hi < 10 ** 9 else f"{lo:.0f}+"
            out(f"    {lab+' ms':>16}{int(m.sum()):>8}{100*p:>10.1f}{se:>8.1f}")
            xs.append(math.sqrt(max(lo, 25) * min(hi, max(lo, 25) * 4)))
            ys.append(100 * p); es.append(se)
        if xs:
            fig, ax = plt.subplots(figsize=(9.5, 4.0), dpi=130)
            ax.errorbar(xs, ys, yerr=es, fmt="o-", color="#d62728", capsize=3,
                        label="300-rate, profile sample")
            ax.axvline(ALT_MS, color="grey", ls=":", label=f"{ALT_MS:.0f} ms = one-hand limit")
            ax.set_xscale("log"); ax.set_ylim(0, 100)
            ax.set_xlabel("spacing to the previous note (ms)")
            ax.set_ylabel("300 rate (%)")
            ax.set_title(f"accuracy vs note spacing - {len(P)} random replays")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(outdir / "plots" / "profile_vs_spacing.png")
            out(f"\n  wrote {outdir/'plots'/'profile_vs_spacing.png'}")

    # ---------------- PROGRESSION ----------------
    if prog:
        out()
        out("=" * 100)
        out(f"PROGRESSION - the same maps played months apart ({len(prog)} replays)")
        out("=" * 100)
        by = defaultdict(list)
        for r in prog:
            if r["_d"] and r["_hash"]:
                by[r["_hash"]].append(r)
        slopes = defaultdict(list)
        firstlast = defaultdict(list)
        for h, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            rs.sort(key=lambda x: x["_d"])
            if len(rs) < 3:
                continue
            d0 = rs[0]["_d"]
            days = np.array([(r["_d"] - d0).days for r in rs], dtype=float)
            out(f"\n  {rs[0]['map'][:44]} [{rs[0]['version'][:14]}]  {rs[0]['_sr']:.2f}*")
            out(f"    {'date':<11}{'mods':<7}{'acc':>7}{'300%':>7}{'bias':>7}{'sd':>7}"
                f"{'fast300':>9}{'ongrid':>8}{'n_fast':>7}")
            for r in rs:
                out(f"    {r['_d'].strftime('%Y-%m-%d'):<11}{r['mods']:<7}"
                    f"{fnum(r['acc_reported']):>7.2f}{fnum(r['rate300_all']):>7.1f}"
                    f"{fnum(r['bias']):>+7.1f}{fnum(r['sd']):>7.1f}"
                    f"{fnum(r['alt_rate']):>9.1f}{fnum(r['ongrid_pct']):>8.1f}"
                    f"{fnum(r['alt_n']):>7.0f}")
            for key in ("rate300_all", "sd", "ongrid_pct", "acc_reported"):
                y = np.array([fnum(r[key]) for r in rs])
                m = np.isfinite(y)
                if m.sum() >= 3 and np.ptp(days[m]) > 30:
                    sl = np.polyfit(days[m], y[m], 1)[0] * 365.0
                    slopes[key].append(sl)
            # first third vs last third
            k = max(1, len(rs) // 3)
            for key in ("rate300_all", "sd", "ongrid_pct"):
                a = np.nanmean([fnum(r[key]) for r in rs[:k]])
                b = np.nanmean([fnum(r[key]) for r in rs[-k:]])
                if np.isfinite(a) and np.isfinite(b):
                    firstlast[key].append(b - a)
            out("    mods changed over the set: " +
                ("yes - " if len({r['mods'] for r in rs}) > 1 else "no  - ") +
                ",".join(sorted({r['mods'] for r in rs})))
        out()
        out("  TREND ACROSS ALL PROGRESSION MAPS  (per map, then summarised)")
        names = {"rate300_all": "300-rate (points/year)", "sd": "scatter (ms/year)",
                 "ongrid_pct": "on-grid % (points/year)", "acc_reported": "accuracy (points/year)"}
        for key, label in names.items():
            if key not in slopes:
                continue
            v = np.array(slopes[key])
            out(f"    {label:<28} median {np.median(v):+7.2f}"
                f"   improved in {int((v>0).sum()) if key!='sd' else int((v<0).sum())}"
                f" of {len(v)} maps")
        for key, label in (("rate300_all", "300-rate, last third minus first third (points)"),
                           ("sd", "scatter, last third minus first third (ms)")):
            if key in firstlast:
                v = np.array(firstlast[key])
                out(f"    {label:<52} median {np.median(v):+6.2f}   n={len(v)}")

        if by:
            fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), dpi=130)
            for h, rs in by.items():
                rs = [r for r in rs if r["_d"]]
                if len(rs) < 3:
                    continue
                rs.sort(key=lambda x: x["_d"])
                xs = [r["_d"] for r in rs]
                axes[0].plot(xs, [fnum(r["rate300_all"]) for r in rs], "o-", lw=1, ms=3, alpha=0.7)
                axes[1].plot(xs, [fnum(r["sd"]) for r in rs], "o-", lw=1, ms=3, alpha=0.7)
            axes[0].set_ylabel("300 rate (%)"); axes[0].set_title("same map, over time: 300 rate")
            axes[1].set_ylabel("scatter (ms)"); axes[1].set_title("same map, over time: scatter")
            for ax in axes:
                ax.tick_params(axis="x", rotation=45, labelsize=7)
            fig.tight_layout()
            fig.savefig(outdir / "plots" / "progression.png")
            out(f"\n  wrote {outdir/'plots'/'progression.png'}")

    (outdir / "sample_report.txt").write_text("\n".join(L), encoding="utf-8")
    out(f"\nwrote {outdir/'sample_report.txt'}")


if __name__ == "__main__":
    main()
