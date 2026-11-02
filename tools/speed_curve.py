"""Pooled 300-rate as a function of how close together the notes are.

Averaging "stream accuracy" over plays mixes maps whose fastest notes are 66 ms
apart with maps whose fastest notes are 177 ms apart, which is why that average
swings wildly between plays. Fitting accuracy against the actual note spacing
uses every tap instead of every play, so the curve is pinned down by thousands
of notes rather than by how many replays happen to be in the folder.

Also reports where the keyboard hand starts being used at all.

Usage:  python speed_curve.py [replaysdir] [mapsdir]
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze
import batch

BINS = [(0, 60), (60, 70), (70, 80), (80, 95), (95, 110), (110, 130), (130, 160),
        (160, 200), (200, 260), (260, 330), (330, 450), (450, 10 ** 9)]


def main():
    indir = Path(sys.argv[1] if len(sys.argv) > 1 else "replays")
    mapsdir = Path(sys.argv[2] if len(sys.argv) > 2 else "maps")
    outdir = Path("out")

    gaps_all, z_all, btn_all, play_all, play_gap_all = [], [], [], [], []
    per_play = []

    for f in sorted(indir.glob("*.osr")):
        rep, mp = batch.get_map(f, mapsdir, outdir / "osz")
        if mp is None or rep.mode != 0:
            continue
        import osu_beatmap
        bm = osu_beatmap.parse(mp)
        objs = bm.objects
        tap = [o for o in objs if not o.is_spinner]
        n_spin = len(objs) - len(tap)
        raw = np.array([o.time for o in tap])
        pr = analyze.press_events(rep)
        game = {"300": rep.count_300, "100": rep.count_100, "50": rep.count_50,
                "miss": rep.count_miss}
        rate, delta, residual, _ = analyze.calibrate(raw, pr, bm.od, rep.mods, game, n_spin)
        nt = raw / rate
        err, btn, hit, pt, stray, _ = analyze.judge(nt, pr, delta=delta)
        w = analyze.W300
        gaps = np.diff(nt, prepend=nt[0] - 1e9)
        gmin = batch.grid_min(gaps)
        fast = (gaps < 110) & hit
        sel = hit
        gaps_all.append(gaps[sel])
        z_all.append(np.abs(err[sel]) / w)
        btn_all.append(btn[sel])
        play_all.append(np.full(int(sel.sum()), len(per_play)))
        play_gap_all.append(np.full(int(sel.sum()), gmin))
        fr = np.abs(err[fast])
        per_play.append({
            "name": f"{bm.title[:26]} [{bm.version[:10]}]",
            "date": rep.played_at.strftime("%Y-%m-%d") if rep.played_at else "",
            "grid": gmin, "mods": rep.mods_str,
            "fast_rate": float(100 * (fr <= w).mean()) if fast.any() else float("nan"),
            "fast_n": int(fast.sum()),
        })
        print(f"  {rep.played_at:%Y-%m-%d}  {bm.title[:26]:<27} grid {gmin:5.0f} ms"
              f"  fast notes {int(fast.sum()):5d}")

    G = np.concatenate(gaps_all)
    Z = np.concatenate(z_all)
    B = np.concatenate(btn_all)
    P = np.concatenate(play_all)

    print()
    print("=" * 92)
    print("300-RATE AS A FUNCTION OF NOTE SPACING  (pooled over every replay)")
    print("=" * 92)
    print(f"{'spacing':>18}{'taps':>7}{'300-rate':>10}{'95% ci':>8}"
          f"{'median |err|':>14}{'keyboard share':>16}")
    xs, ys, los, hi, ns, kbs = [], [], [], [], [], []
    for lo, hi in BINS:
        m = (G >= lo) & (G < hi)
        n = int(m.sum())
        if n < 20:
            continue
        p = (Z[m] <= 1).mean()
        se = np.sqrt(p * (1 - p) / n)
        kb = 100 * (B[m] == 2).mean()
        med = 100 * np.median(Z[m])
        lab = f"{lo:.0f}-{hi:.0f}" if hi < 10 ** 9 else f"{lo:.0f}+"
        print(f"{lab+f' ms':>18}{n:>7}{100*p:>10.1f}{100*1.96*se:>8.1f}"
              f"{med:>13.1f}%{kb:>15.1f}%")
        xs.append(np.sqrt(max(lo, 25) * min(hi, max(lo, 25) * 4)))
        ys.append(100 * p); los.append(100 * max(0, p - 1.96 * se))
        hi.append(100 * min(1, p + 1.96 * se)); ns.append(n)
        kbs.append(kb)

    # does controlling for spacing explain the between-play variance?
    pp = [r for r in per_play if np.isfinite(r["fast_rate"]) and r["fast_n"] >= 30]
    if len(pp) >= 5:
        g = np.array([r["grid"] for r in pp])
        r_ = np.array([r["fast_rate"] for r in pp])
        corr = np.corrcoef(g, r_)[0, 1]
        print()
        print(f"per-play fast-note 300-rate vs that map's fastest spacing:"
              f"  r = {corr:+.3f}  (n = {len(pp)} plays)")
        print(f"  spread of the per-play fast-note rate: sd {r_.std():.1f} points")
        # residual spread after removing the spacing trend
        fit = np.polyfit(g, r_, 1)
        resid = r_ - np.polyval(fit, g)
        print(f"  after removing the trend with spacing, sd falls to {resid.std():.1f} points")
        print(f"  -> {100*(1 - resid.std()**2 / r_.std()**2):.0f}% of the play-to-play"
              f" variation in 'stream accuracy' is just which map it was")

    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=130)
    ax.errorbar(xs, ys, yerr=[np.array(ys) - los, np.array(hi) - np.array(ys)],
                fmt="o-", color="#d62728", capsize=3, label="300-rate (95% ci)")
    ax2 = ax.twinx()
    ks = [100 * (B[(G >= lo) & (G < hi)] == 2).mean()
          for lo, hi in BINS if int(((G >= lo) & (G < hi)).sum()) >= 20]
    ax2.plot(xs, ks, "s--", color="#1f77b4", label="share of taps on the keyboard hand")
    ax2.set_ylabel("keyboard share (%)", color="#1f77b4")
    ax2.set_ylim(0, 100)
    ax.set_xscale("log")
    ax.set_xlabel("spacing to the previous note (ms)")
    ax.set_ylabel("300 rate (%)")
    ax.set_ylim(0, 100)
    ax.set_title("accuracy against how close the notes are - every replay pooled")
    ax.legend(fontsize=8, loc="lower left")
    ax2.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    p = outdir / "plots" / "accuracy_vs_spacing.png"
    fig.savefig(p)
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
