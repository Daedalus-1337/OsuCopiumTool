"""Run the analyser over every replay in a folder and aggregate the results.

Per replay it resolves the beatmap (local cache first, then public mirrors with
MD5 verification), satisfies itself that the judgement model reproduces the
score the game recorded, and writes one row of metrics. It then reports the
things only many plays can answer:

  * is the timing bias one constant (a clock/offset problem) or does it move
    from play to play (a habit problem)
  * is one hand worse than the other, compared on the same notes
  * how much of the loss sits on notes too fast to single-tap

Note the analyser measures "needs alternation" with an absolute threshold
(110 ms), a default heuristic for a single-hand tap ceiling rather than a
map's grid: a map whose fastest notes are 1/8 gets single-tapped on purpose and
would otherwise pollute the alternation statistics.

Usage:
    python batch.py replays [mapsdir] [outdir]
"""
from __future__ import annotations

import csv
import hashlib
import sys
import traceback
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze
import fetch_map
import osr_parse
import osu_beatmap

BUTTON_NAMES = analyze.BUTTON_NAMES
ALT_MS = 110.0        # faster than this cannot be tapped with one hand


def get_map(osr: Path, mapsdir: Path, oszdir: Path, local_dirs=None):
    rep = osr_parse.load(osr)
    md5 = rep.beatmap_hash
    for p in mapsdir.glob("*.osu"):
        if hashlib.md5(p.read_bytes()).hexdigest() == md5:
            return rep, p
    dest = fetch_map.resolve(str(osr), str(mapsdir), osz_dir=str(oszdir),
                             local_dirs=local_dirs)
    return rep, dest


def grid_min(gaps):
    rv = np.round(gaps[1:])
    vals, cnts = np.unique(rv, return_counts=True)
    common = [int(v) for v, c in zip(vals, cnts) if c >= max(5, 0.04 * len(gaps))]
    return min(common) if common else 100.0


def band_stats(errors, mask, w300):
    sel = errors[mask]
    if not len(sel):
        return dict(n=0, bias=float("nan"), sd=float("nan"), rate=float("nan"))
    return dict(n=int(len(sel)), bias=float(sel.mean()), sd=float(sel.std()),
                rate=float(100 * (np.abs(sel) <= w300).mean()))


def metrics(rep, bm, taps_sink=None, tag=""):
    objs = bm.objects
    tap = [o for o in objs if not o.is_spinner]
    n_spin = len(objs) - len(tap)
    raw_t = np.array([o.time for o in tap])
    presses = analyze.press_events(rep)
    game = {"300": rep.count_300, "100": rep.count_100, "50": rep.count_50,
            "miss": rep.count_miss}

    rate, delta, residual, modc = analyze.calibrate(
        raw_t, presses, bm.od, rep.mods, game, n_spin)
    nt = raw_t / rate
    err, btn, hit, pt, stray, _ = analyze.judge(nt, presses, delta=delta)
    # put everything into wall-clock milliseconds so DT/HT plays are comparable
    speed = analyze.speed_from_mods(rep.mods)
    if abs(speed - 1.0) > 1e-9:
        err = err / speed
        pt = pt / speed
        nt = nt / speed
        analyze.W300 = analyze.W300 / speed
        analyze.W100 = analyze.W100 / speed
        analyze.W50 = analyze.W50 / speed
    w300 = analyze.W300
    if not hit.any():
        return None

    gaps = np.diff(nt, prepend=nt[0] - 1e9)
    gmin = grid_min(gaps)
    dense = (gaps < 1.3 * gmin)
    dense[0] = False
    needs_alt = gaps < ALT_MS
    needs_alt[0] = False
    runs, _ = analyze.build_runs(nt, ALT_MS)

    m = hit
    errors = err[m]
    buttons = btn[m]
    dense_m = dense[m]
    alt_m = needs_alt[m]

    d = {
        "n_objects": len(objs), "n_taps": int(m.sum()), "spinners": n_spin,
        "acc_reported": rep.acc, "acc_model": analyze.acc_of(modc),
        "count_residual": residual, "clock_delta": delta, "axis_rate": rate,
        "w300": w300,
        "bias": float(errors.mean()), "median": float(np.median(errors)),
        "sd": float(errors.std()), "late_pct": float(100 * (errors > 0).mean()),
        "off300_pct": float(100 * (np.abs(errors) > w300).mean()),
        "off50_window_pct": float(100 * (np.abs(errors) > analyze.W50).mean()),
        "stray": stray, "grid_ms": gmin,
        "dense_notes": int(dense.sum()),
        "rate300_all": float(100 * (np.abs(errors) <= w300).mean()),
    }
    d.update({f"dense_{k}": v for k, v in band_stats(errors, dense_m, w300).items()})
    d.update({f"sparse_{k}": v for k, v in band_stats(errors, ~dense_m, w300).items()})
    d.update({f"alt_{k}": v for k, v in band_stats(errors, alt_m, w300).items()})
    d.update({f"slow_{k}": v for k, v in band_stats(errors, ~alt_m, w300).items()})
    for bit in (1, 2, 4, 8):
        sel = errors[buttons == bit]
        d[f"n_b{bit}"] = int(len(sel))
        d[f"bias_b{bit}"] = float(sel.mean()) if len(sel) else float("nan")
        d[f"sd_b{bit}"] = float(sel.std()) if len(sel) else float("nan")
        d[f"rate300_b{bit}"] = float(100 * (np.abs(sel) <= w300).mean()) if len(sel) else float("nan")
        # same hand, restricted to the notes where alternation is required
        sel2 = errors[alt_m & (buttons == bit)]
        d[f"alt_n_b{bit}"] = int(len(sel2))
        d[f"alt_bias_b{bit}"] = float(sel2.mean()) if len(sel2) else float("nan")
        d[f"alt_sd_b{bit}"] = float(sel2.std()) if len(sel2) else float("nan")
        d[f"alt_rate300_b{bit}"] = float(100 * (np.abs(sel2) <= w300).mean()) if len(sel2) else float("nan")

    devs, alt_rate, offs, sim, nint = [], [], [], 0, 0
    for r in runs:
        ids = [i for i in r if hit[i]]
        if len(ids) >= 2:
            ts = pt[ids]
            g = float(np.mean(np.diff(nt[r])))
            dv = (np.diff(ts) / g) - 1.0
            devs.extend(dv.tolist())
            sim += int((np.diff(ts) < 25).sum())
            nint += len(dv)
            s = [int(btn[i]) for i in ids]
            alt_rate.append(sum(1 for x, y in zip(s, s[1:]) if x != y) / (len(s) - 1))
        if len(ids) >= 4:
            e, b = err[ids], btn[ids]
            if (b == 1).sum() >= 2 and (b == 2).sum() >= 2:
                offs.append(float(np.median(e[b == 2]) - np.median(e[b == 1])))

    # accuracy by stream length: 2-note bursts through long unbroken runs
    for lo, hi in ((2, 2), (3, 5), (6, 10), (11, 999)):
        ids = [i for r in runs if lo <= len(r) <= hi for i in r if hit[i]]
        key = f"run{lo}_{hi if hi < 999 else 'plus'}"
        if ids:
            z = np.abs(err[ids]) / w300
            d[f"{key}_n"] = len(ids)
            d[f"{key}_rate"] = float(100 * (z <= 1).mean())
            d[f"{key}_bias"] = float(err[ids].mean())
        else:
            d[f"{key}_n"] = 0
            d[f"{key}_rate"] = float("nan")
            d[f"{key}_bias"] = float("nan")
    d["alt_runs"] = len(runs)
    d["alt_run_notes"] = sum(len(r) for r in runs)
    d["ongrid_pct"] = float(100 * (np.abs(devs) <= 0.15).mean()) if len(devs) else float("nan")
    d["offgrid50_pct"] = float(100 * (np.abs(devs) > 0.50).mean()) if len(devs) else float("nan")
    d["alternation_pct"] = float(100 * np.mean(alt_rate)) if alt_rate else float("nan")
    d["interhand_offset"] = float(np.median(offs)) if offs else float("nan")
    d["simult_pct"] = float(100 * sim / nint) if nint else float("nan")

    shift = -np.median(errors)
    d["acc_bias_removed"] = analyze.acc_for_errors(err + shift, n_spin)
    d["gain_bias_removed"] = d["acc_bias_removed"] - d["acc_model"]

    if taps_sink is not None:
        run_of = np.full(len(nt), -1)
        for ri, r in enumerate(runs):
            for i in r:
                run_of[i] = ri
        run_len = np.zeros(len(nt), dtype=int)
        for r in runs:
            run_len[r] = len(r)
        idx = np.where(hit)[0]
        for i in idx:
            taps_sink.append((tag, float(gaps[i]), float(abs(err[i]) / w300),
                              int(btn[i]), int(run_len[i]), int(run_of[i]),
                              float(err[i])))
    return d, errors, buttons, alt_m


def main():
    indir = Path(sys.argv[1] if len(sys.argv) > 1 else "replays")
    mapsdir = Path(sys.argv[2] if len(sys.argv) > 2 else "maps")
    outdir = Path(sys.argv[3] if len(sys.argv) > 3 else "out")
    songs = [a for a in sys.argv[4:] if a.strip()]
    mapsdir.mkdir(exist_ok=True)
    outdir.mkdir(exist_ok=True)
    (outdir / "osz").mkdir(exist_ok=True)
    if songs:
        print(f"also scanning locally: {songs}")

    files = sorted(indir.glob("*.osr"))
    print(f"{len(files)} replays in {indir}\n")

    rows, pooled, skipped = [], [], []
    taps = []
    for f in files:
        print(f"--- {f.name}")
        try:
            rep, mp = get_map(f, mapsdir, outdir / "osz", songs)
            if mp is None:
                skipped.append((f.name, "beatmap not resolvable (see note above)"))
                continue
            if rep.mode != 0:
                skipped.append((f.name, f"mode {rep.mode} is not osu!standard"))
                continue
            bm = osu_beatmap.parse(mp)
            res = metrics(rep, bm, taps_sink=taps, tag=f.name)
            if res is None:
                skipped.append((f.name, "no matched taps"))
                continue
            d, errs, btns, alt_m = res
            pooled.append((errs, btns, alt_m, d["w300"]))
            d.update({
                "file": f.name, "map": f"{bm.artist} - {bm.title}",
                "version": bm.version, "mapper": bm.creator,
                "date": rep.played_at.strftime("%Y-%m-%d") if rep.played_at else "",
                "mods": rep.mods_str, "od": bm.od, "ar": bm.ar,
                "username": rep.username, "mode": rep.mode,
            })
            rows.append(d)
            print(f"    300 rate  all {100*(np.abs(errs)<=d['w300']).mean():5.1f}%"
                  f"  alt {d['alt_rate']:5.1f}%  sparse {d['sparse_rate']:5.1f}%"
                  f"   bias {d['bias']:+6.1f}  sd {d['sd']:5.1f}"
                  f"   M1 {d['rate300_b1']:5.1f}%  M2 "
                  f"{d['rate300_b2'] if not np.isnan(d['rate300_b2']) else float('nan'):5.1f}%")
        except Exception as e:
            skipped.append((f.name, f"{type(e).__name__}: {e}"))
            print(f"    FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()

    if not rows:
        print("nothing analysed")
        return

    keys = list(rows[0].keys())
    with (outdir / "batch.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    with (outdir / "taps.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "gap_ms", "z", "button", "run_len", "run_id", "err_ms"])
        w.writerows(taps)

    E = np.concatenate([p[0] for p in pooled])
    B = np.concatenate([p[1] for p in pooled])
    A = np.concatenate([p[2] for p in pooled])
    Z = np.concatenate([np.abs(p[0]) / p[3] for p in pooled])   # |error| in units of that
    # play's 300 window, so plays with different OD can be pooled
    L = []

    def out(s=""):
        print(s); L.append(s)

    def f2(v, w=8, p=1):
        return f"{v:>{w}.{p}f}" if v is not None and not np.isnan(v) else " " * (w - 1) + "-"

    out("\n" + "=" * 108)
    out(f"AGGREGATE over {len(rows)} plays  ({len(E)} taps, {int(A.sum())} of them on notes"
        f" too fast to single-tap)")
    out("=" * 108)
    out("PER PLAY, oldest to newest   (300% = share of taps landing inside the 300 window)")
    out(f"{'date':<11}{'map':<32}{'ver':<13}{'mods':<6}{'acc':>6}{'bias':>7}{'sd':>6}"
        f"{'300%all':>8}{'alt300%':>8}{'sparse':>8}{'M1':>7}{'M2':>7}{'alt%':>7}{'gain':>7}")
    for r in sorted(rows, key=lambda x: x["date"]):
        out(f"{r['date']:<11}{r['map'][:31]:<32}{r['version'][:12]:<13}{r['mods']:<6}"
            f"{r['acc_reported']:>6.2f}{r['bias']:>+7.1f}{r['sd']:>6.1f}"
            f"{f2(r['rate300_all'],8)}{f2(r['alt_rate'],8)}{f2(r['sparse_rate'],8)}"
            f"{f2(r['rate300_b1'],7)}{f2(r['rate300_b2'],7)}"
            f"{f2(r['alternation_pct'],7)}{r['gain_bias_removed']:>+7.2f}")

    biases = np.array([r["bias"] for r in rows])
    medians = np.array([r["median"] for r in rows])
    out()
    out("1. IS THE TIMING BIAS ONE CONSTANT?  (a constant would mean a clock/offset problem)")
    out(f"   per-play bias   : min {biases.min():+.1f}  max {biases.max():+.1f}"
        f"  mean {biases.mean():+.1f}  sd {biases.std():.1f} ms")
    out(f"   per-play median : min {medians.min():+.1f}  max {medians.max():+.1f}"
        f"  mean {medians.mean():+.1f}")
    out(f"   plays where the typical tap was late: {int((medians>0).sum())} of {len(rows)}")
    out(f"   pooled bias in ms: {E.mean():+.1f} (median {np.median(E):+.1f},"
        f" late {100*(E>0).mean():.1f}%)")
    out(f"   per-play bias spread ({biases.std():.1f} ms) is"
        f" {'small next to' if biases.std() < E.std()/3 else 'comparable to'} the"
        f" within-play spread ({E.std():.1f} ms) - so there is no single offset to dial out")
    fb = np.array([r["alt_bias"] - r["sparse_bias"] for r in rows
                   if not np.isnan(r["alt_bias"]) and not np.isnan(r["sparse_bias"])])
    if len(fb):
        out(f"   BUT within a play, the fast notes sit {fb.mean():+.1f} ms later than the rest"
            f" (per-play {fb.min():+.1f} to {fb.max():+.1f}; later in"
            f" {int((fb>0).sum())} of {len(fb)} plays)")
        out("   -> lateness is not a fixed offset, it grows with how fast the notes come")

    out()
    out("2. TWO HANDS, COMPARED ON THE SAME NOTES")
    for nm, maskfn in (("all notes", lambda: np.ones(len(E), bool)),
                       (f"notes faster than {ALT_MS:.0f} ms (where the keyboard is used)",
                        lambda: A)):
        m = maskfn()
        out(f"   {nm}:")
        for bit in (1, 2, 4, 8):
            sel = E[m & (B == bit)]
            if len(sel) < 5:
                continue
            out(f"     {BUTTON_NAMES[bit]}: n={len(sel):5d}  bias {sel.mean():+6.1f}"
                f"  sd {sel.std():5.1f}  300-rate {100*(Z[m & (B==bit)]<=1).mean():5.1f}%")
    m2a = E[A & (B == 2)]
    m1a = E[A & (B == 1)]
    if len(m2a) > 5 and len(m1a) > 5:
        r1 = 100 * (Z[A & (B == 1)] <= 1).mean()
        r2 = 100 * (Z[A & (B == 2)] <= 1).mean()
        out(f"   on the fast notes: bias differs by {m2a.mean()-m1a.mean():+.1f} ms,"
            f" jitter by {m2a.std()-m1a.std():+.1f} ms, 300-rate by {r2-r1:+.1f} points"
            f" (keyboard minus pen)")

    out()
    out("3. FAST NOTES vs THE REST")
    alt = np.array([r["alt_rate"] for r in rows if not np.isnan(r["alt_rate"])])
    slow = np.array([r["sparse_rate"] for r in rows if not np.isnan(r["sparse_rate"])])
    dense = np.array([r["dense_rate"] for r in rows if not np.isnan(r["dense_rate"])])
    out(f"   pooled 300-rate, notes faster than {ALT_MS:.0f} ms :"
        f" {100*(Z[A]<=1).mean():5.1f}%   ({int(A.sum())} taps)")
    out(f"   pooled 300-rate, everything else          :"
        f" {100*(Z[~A]<=1).mean():5.1f}%   ({int((~A).sum())} taps)")
    out(f"   shortfall                                 :"
        f" {100*(Z[~A]<=1).mean()-100*(Z[A]<=1).mean():5.1f} points")
    if len(alt):
        out(f"   per-play means (unweighted, only plays that have such notes):"
            f" {alt.mean():.1f}% vs {slow.mean():.1f}%")

    out()
    out("4. ALTERNATION AND PHASE, ONLY ON RUNS THAT REQUIRE ALTERNATION")
    og = np.array([r["ongrid_pct"] for r in rows if not np.isnan(r["ongrid_pct"])])
    og50 = np.array([r["offgrid50_pct"] for r in rows if not np.isnan(r["offgrid50_pct"])])
    al = np.array([r["alternation_pct"] for r in rows if not np.isnan(r["alternation_pct"])])
    ih = np.array([r["interhand_offset"] for r in rows if not np.isnan(r["interhand_offset"])])
    sp = np.array([r["simult_pct"] for r in rows if not np.isnan(r["simult_pct"])])
    if len(al):
        out(f"   alternation rate inside runs      : {al.mean():.1f}%"
            f"   (100% = never two taps in a row on the same input)")
    if len(og):
        out(f"   in-run tap intervals within 15% of the grid: {og.mean():.1f}%")
    if len(og50):
        out(f"   in-run intervals more than 50% off the grid: {og50.mean():.1f}%"
            f"   (taps collapsing together / a note dropped)")
    if len(ih):
        out(f"   inter-hand offset within a run   : median {np.median(ih):+.1f} ms"
            f"   (per-play range {ih.min():+.1f} to {ih.max():+.1f})")
    if len(sp):
        out(f"   near-simultaneous tap pairs      : {sp.mean():.1f}% of in-run intervals")

    gain = np.array([r["gain_bias_removed"] for r in rows])
    out()
    out("5. WHAT REMOVING THE BIAS WOULD BE WORTH")
    out(f"   mean {gain.mean():+.2f} accuracy points"
        f"   median {np.median(gain):+.2f}   range {gain.min():+.2f} to {gain.max():+.2f}")
    out(f"   worst offenders: "
        + ", ".join(f"{r['map'][:22]} {r['date']} ({r['gain_bias_removed']:+.1f})"
                    for r in sorted(rows, key=lambda x: -x["gain_bias_removed"])[:3]))
    if skipped:
        out()
        out("NOT ANALYSED")
        for n, why in skipped:
            out(f"   {n}\n     {why}")

    (outdir / "batch_report.txt").write_text("\n".join(L), encoding="utf-8")

    # ---- plots -----------------------------------------------------------
    plots = outdir / "plots"
    plots.mkdir(exist_ok=True)
    order = sorted(range(len(rows)), key=lambda i: rows[i]["date"])
    dates = [rows[i]["date"] for i in order]

    fig, ax = plt.subplots(figsize=(9.5, 3.8), dpi=130)
    x = np.arange(len(order))
    b = [rows[i]["bias"] for i in order]
    s = [rows[i]["sd"] for i in order]
    ax.errorbar(x, b, yerr=s, fmt="o", color="#d62728", ecolor="grey",
                elinewidth=1, capsize=3, label="per-play mean error +/- 1 sd of its taps")
    ax.axhline(0, color="k", lw=0.8)
    ax.axhspan(-28.4, 28.4, color="green", alpha=0.10, label="300 window at OD 8.6")
    ax.set_xticks(x)
    ax.set_xticklabels(dates, rotation=70, fontsize=7)
    ax.set_ylabel("hit error (ms)")
    ax.set_title("bias per play - a flat line would mean one constant offset")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "bias_per_play.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 3.8), dpi=130)
    bins = np.arange(-120, 121, 5)
    for bit in (1, 2, 4, 8):
        sel = E[B == bit]
        if len(sel) < 10:
            continue
        ax.hist(sel, bins=bins, histtype="step", lw=1.8,
                label=f"{BUTTON_NAMES[bit]} (n={len(sel)})", color=analyze.COLORS.get(bit))
    ax.axvspan(-28.4, 28.4, color="green", alpha=0.10, label="300 window at OD 8.6")
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("hit error (ms)")
    ax.set_ylabel("taps (all replays)")
    ax.set_title("pooled hit error by input across every replay")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "pooled_errors.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 3.8), dpi=130)
    ax.bar(x - 0.2, [rows[i]["alt_rate"] for i in order], 0.4,
           label=f"notes faster than {ALT_MS:.0f} ms", color="#d62728")
    ax.bar(x + 0.2, [rows[i]["sparse_rate"] for i in order], 0.4,
           label="everything else", color="#1f77b4")
    ax.set_xticks(x)
    ax.set_xticklabels(dates, rotation=70, fontsize=7)
    ax.set_ylabel("300 rate (%)")
    ax.set_ylim(0, 100)
    ax.set_title("300 rate on the fastest notes vs the rest, per play")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(plots / "fast_vs_rest.png"); plt.close(fig)

    for p in sorted(plots.glob("*.png")):
        print(f"wrote {p}")
    print(f"\nwrote {outdir/'batch.csv'}\nwrote {outdir/'batch_report.txt'}")


if __name__ == "__main__":
    main()
