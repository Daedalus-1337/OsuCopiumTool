"""Between-group comparison for the tapx stream-accuracy experiment.

    python exp_compare.py <batch_dir_T> <label_T> <batch_dir_K> <label_K> <outdir>

Loads two groups using the definitions in exp_metrics.py and reports:

  * pooled accuracy by absolute spacing band, side by side
  * accuracy by unbroken stream-run length (separates endurance from the pair)
  * slow and stream 300-rate, the stream penalty, in-run on-grid share,
    alternation rate, near-simultaneous share, entry failure, phase_ms, noise_ms
    and phase/noise, each with a cluster-bootstrap interval within each group
    and on the difference between groups
  * the verdict for each decision rule of HYPOTHESIS.md

Design note: everything is computed from per-play sufficient statistics (counts
and sums of squares), so a bootstrap iteration only adds up over plays rather
than re-scanning taps. Resampling is over plays, which is the correct unit --
taps inside one play share a clock, a map and a day, and are not independent.
"""
import math
import sys
from pathlib import Path

import numpy as np

import exp_metrics as em

NBOOT = 4000
RNG = np.random.default_rng(20261102)
CUT = 110.0          # slow/stream threshold, ms
NEAR_SIM = 25.0
DEF_W300 = 28.4


def play_suff(taps, w, cutoff=CUT):
    """Sufficient statistics for one play: counts and sums only."""
    s = {"n_taps": 0, "n_slow": 0, "ok_slow": 0, "n_stream": 0, "ok_stream": 0,
         "n_int": 0, "same": 0, "sum_d2": 0.0, "n_dev15": 0, "n_dev50": 0,
         "n_nearsim": 0, "n_alt": 0, "sum_alt2": 0.0, "sum_com2": 0.0,
         "n_first": 0, "first_off15": 0, "first_off30": 0, "n_runs": 0,
         "sum_slow_e": 0.0, "sum_slow_e2": 0.0, "sum_alt": 0.0,
         "slow_ss_within": 0.0, "n_slow_within": 0,
         "stream_ss_within": 0.0, "n_stream_within": 0,
         "band": {em.band_label(lo, hi): np.zeros(3) for lo, hi in em.BANDS},
         "bucket": {2: np.zeros(2), "3-5": np.zeros(2),
                    "6-10": np.zeros(2), "11+": np.zeros(2)}}
    for gap, btn, err in taps:
        ok = abs(err) <= w
        s["n_taps"] += 1
        if gap < cutoff:
            s["n_stream"] += 1; s["ok_stream"] += int(ok)
        else:
            s["n_slow"] += 1; s["ok_slow"] += int(ok)
            s["sum_slow_e"] += err; s["sum_slow_e2"] += err * err
        for lo, hi in em.BANDS:
            if lo <= gap < hi:
                v = s["band"][em.band_label(lo, hi)]
                v[0] += 1; v[1] += int(ok); v[2] += int(btn == 2)
                break
    slow_e = [e for g, b, e in taps if g >= cutoff]
    if len(slow_e) > 1:
        mu = sum(slow_e) / len(slow_e)
        s["slow_ss_within"] = sum((e - mu) ** 2 for e in slow_e)
        s["n_slow_within"] = len(slow_e)
    fast_e = [e for g, b, e in taps if g < cutoff]
    if len(fast_e) > 1:
        mu = sum(fast_e) / len(fast_e)
        s["stream_ss_within"] = sum((e - mu) ** 2 for e in fast_e)
        s["n_stream_within"] = len(fast_e)
    for run in em.stream_runs(taps):
        s["n_runs"] += 1
        L = len(run)
        b = 2 if L == 2 else ("3-5" if L <= 5 else ("6-10" if L <= 10 else "11+"))
        bv = s["bucket"][b]
        for gap, btn, err in run:
            bv[0] += 1; bv[1] += int(abs(err) <= w)
        iv = em.run_intervals(run)
        if len(iv) > 1 and run[0][1] != run[1][1]:
            f, fg = run[1][2] - run[0][2], run[1][0]
            s["n_first"] += 1
            s["first_off15"] += int(abs(f / fg) > 0.15)
            s["first_off30"] += int(abs(f / fg) > 0.30)
        for d, dg, bp, bn in iv:
            s["n_int"] += 1
            s["sum_d2"] += d * d
            if bp == bn:
                s["same"] += 1
            dev = abs(d / dg)
            if dev <= 0.15:
                s["n_dev15"] += 1
            if dev > 0.50:
                s["n_dev50"] += 1
            if abs(dg + d) < NEAR_SIM:
                s["n_nearsim"] += 1
        for d0, d1, g in em.alt_pairs(iv):
            s["n_alt"] += 1
            s["sum_alt"] += (d0 - d1) / 2.0
            s["sum_alt2"] += ((d0 - d1) / 2.0) ** 2
            s["sum_com2"] += ((d0 + d1) / 2.0) ** 2
    return s


def add(a, b):
    """Sum two sufficient-statistics dicts."""
    o = dict(a)
    for k in ("n_taps", "n_slow", "ok_slow", "n_stream", "ok_stream", "n_int",
              "same", "sum_d2", "n_dev15", "n_dev50", "n_nearsim", "n_alt",
              "sum_alt2", "sum_com2", "n_first", "first_off15", "first_off30",
              "n_runs", "sum_slow_e", "sum_slow_e2", "sum_alt",
              "slow_ss_within", "n_slow_within",
              "stream_ss_within", "n_stream_within"):
        o[k] = a[k] + b[k]
    o["band"] = {k: a["band"][k] + b["band"][k] for k in a["band"]}
    o["bucket"] = {k: a["bucket"][k] + b["bucket"][k] for k in a["bucket"]}
    return o


def zero(s):
    o = dict(s)
    for k in o:
        if k not in ("band", "bucket"):
            o[k] = 0
    o["band"] = {k: np.zeros(3) for k in s["band"]}
    o["bucket"] = {k: np.zeros(2) for k in s["bucket"]}
    return o


def metrics(s):
    """Derived quantities from accumulated sufficient statistics."""
    def pct(num, den):
        return 100.0 * num / den if den else float("nan")
    m = {
        "n_plays": s.get("n_plays", 0), "n_taps": s["n_taps"],
        "n_slow": s["n_slow"], "n_stream": s["n_stream"], "n_runs": s["n_runs"],
        "slow_rate": pct(s["ok_slow"], s["n_slow"]),
        "stream_rate": pct(s["ok_stream"], s["n_stream"]),
        "on_grid15": pct(s["n_dev15"], s["n_int"]),
        "off_grid50": pct(s["n_dev50"], s["n_int"]),
        "near_simult": pct(s["n_nearsim"], s["n_int"]),
        "alt_rate": pct(s["n_int"] - s["same"], s["n_int"]),
        "interval_rms": math.sqrt(s["sum_d2"] / s["n_int"]) if s["n_int"] else float("nan"),
        "entry_off15": pct(s["first_off15"], s["n_first"]),
        "entry_off30": pct(s["first_off30"], s["n_first"]),
        "n_first": s["n_first"],
    }
    m["stream_penalty"] = m["slow_rate"] - m["stream_rate"]
    if s["n_slow"]:
        mu = s["sum_slow_e"] / s["n_slow"]
        m["slow_sd"] = math.sqrt(max(0.0, s["sum_slow_e2"] / s["n_slow"] - mu * mu))
        m["slow_bias"] = mu
    else:
        m["slow_sd"] = m["slow_bias"] = float("nan")
    m["slow_sd_within"] = (math.sqrt(s["slow_ss_within"] / s["n_slow_within"])
                           if s["n_slow_within"] else float("nan"))
    m["stream_sd_within"] = (math.sqrt(s["stream_ss_within"] / s["n_stream_within"])
                             if s["n_stream_within"] else float("nan"))
    # PRIMARY OUTCOME: how much the alternation demand degrades a player's own
    # tap precision, relative to the same player's slow notes. Dimensionless and
    # within-player, so it carries no overall skill difference.
    m["degradation"] = (m["stream_sd_within"] / m["slow_sd_within"]
                        if m["slow_sd_within"] else float("nan"))
    if s["n_alt"]:
        ra = math.sqrt(s["sum_alt2"] / s["n_alt"])
        rc = math.sqrt(s["sum_com2"] / s["n_alt"])
        m["rms_alt"] = ra; m["rms_com"] = rc
        m["phase_pooled"] = math.sqrt(max(0.0, ra * ra - 3 * rc * rc))
        m["noise_pooled"] = rc * math.sqrt(2)
        m["phase_over_noise"] = (m["phase_pooled"] / m["noise_pooled"]
                                 if m["noise_pooled"] else float("nan"))
        # split the alternating term into a systematic hand offset and its
        # variation: rms(alt)^2 = mean(alt)^2 + var(alt)
        am = s["sum_alt"] / s["n_alt"]
        m["alt_mean"] = am
        m["alt_sd"] = math.sqrt(max(0.0, s["sum_alt2"] / s["n_alt"] - am * am))
    else:
        for k in ("rms_alt", "rms_com", "phase_pooled", "noise_pooled",
                  "phase_over_noise", "alt_mean", "alt_sd"):
            m[k] = float("nan")
    return m


def load_group(batch_dir):
    plays, windows = em.load(batch_dir)
    suff, keep = [], []
    for pf, taps in sorted(plays.items()):
        if len(taps) < 30:
            continue
        suff.append(play_suff(taps, windows.get(pf) or DEF_W300))
        keep.append(pf)
    return keep, suff


def boot(keepT, sT, keepK, sK, keys):
    """Cluster bootstrap over plays; returns (T CI, K CI, difference CI) per key."""
    iT, iK = np.arange(len(sT)), np.arange(len(sK))
    out = {k: {"T": np.empty(NBOOT), "K": np.empty(NBOOT), "D": np.empty(NBOOT)}
           for k in keys}
    baseT = zero(sT[0]); baseK = zero(sK[0])
    for b in range(NBOOT):
        aT = baseT.copy(); aK = baseK.copy()
        for i in RNG.choice(iT, len(iT), True):
            aT = add(aT, sT[i])
        for i in RNG.choice(iK, len(iK), True):
            aK = add(aK, sK[i])
        mT, mK = metrics(aT), metrics(aK)
        for k in keys:
            out[k]["T"][b] = mT[k]; out[k]["K"][b] = mK[k]
            out[k]["D"][b] = mT[k] - mK[k]
    def q(v, p):
        v = v[np.isfinite(v)]
        return float(np.percentile(v, p)) if v.size else float("nan")
    return {k: (q(out[k]["T"], 2.5), q(out[k]["T"], 97.5),
                q(out[k]["K"], 2.5), q(out[k]["K"], 97.5),
                q(out[k]["D"], 2.5), q(out[k]["D"], 97.5)) for k in keys}


def main():
    dT, lT, dK, lK = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    outdir = Path(sys.argv[5]); outdir.mkdir(parents=True, exist_ok=True)

    keepT, sT = load_group(dT)
    keepK, sK = load_group(dK)
    allT = zero(sT[0])
    for s in sT:
        allT = add(allT, s)
    allK = zero(sK[0])
    for s in sK:
        allK = add(allK, s)
    mT, mK = metrics(allT), metrics(allK)

    print(f"=== {lT} (tapx)  vs  {lK} (keyboard-alt) ===")
    print(f"  {lT}: {len(sT)} plays, {mT['n_taps']} taps, {mT['n_stream']} stream taps, "
          f"{mT['n_runs']} stream runs")
    print(f"  {lK}: {len(sK)} plays, {mK['n_taps']} taps, {mK['n_stream']} stream taps, "
          f"{mK['n_runs']} stream runs")
    print()

    print("ACCURACY BY SPACING BAND (pooled taps, each play's own 300 window)")
    print(f"  {'band ms':<10}{'T taps':>10}{'T 300':>9}{'K taps':>10}{'K 300':>9}"
          f"{'input2 T/K':>13}")
    for lo, hi in em.BANDS:
        k = em.band_label(lo, hi)
        nT, oT, iT = allT["band"][k]; nK, oK, iK = allK["band"][k]
        if nT or nK:
            fT = f"{100*oT/nT:.1f}%" if nT else "-"
            fK = f"{100*oK/nK:.1f}%" if nK else "-"
            sh = f"{100*iT/nT:.0f}/{100*iK/nK:.0f}%" if nT and nK else "-"
            print(f"  {k:<10}{int(nT):>10}{fT:>9}{int(nK):>10}{fK:>9}{sh:>13}")
    print()

    print("ACCURACY BY UNBROKEN STREAM-RUN LENGTH (pooled taps inside runs)")
    print(f"  {'run length':<12}{'T taps':>10}{'T 300':>9}{'K taps':>10}{'K 300':>9}")
    for b in (2, "3-5", "6-10", "11+"):
        nT, oT = allT["bucket"][b]; nK, oK = allK["bucket"][b]
        fT = f"{100*oT/nT:.1f}%" if nT else "-"
        fK = f"{100*oK/nK:.1f}%" if nK else "-"
        print(f"  {str(b):<12}{int(nT):>10}{fT:>9}{int(nK):>10}{fK:>9}")
    print()

    keys = [("slow_rate", "slow 300-rate", "{:.1f}"),
            ("stream_rate", "stream 300-rate", "{:.1f}"),
            ("stream_penalty", "stream penalty pp", "{:+.1f}"),
            ("on_grid15", "in-run on-grid %", "{:.1f}"),
            ("off_grid50", "in-run >50 off %", "{:.1f}"),
            ("interval_rms", "interval rms ms", "{:.1f}"),
            ("alt_rate", "alternation %", "{:.1f}"),
            ("near_simult", "near-simult %", "{:.1f}"),
            ("entry_off15", "entry >15 off %", "{:.1f}"),
            ("entry_off30", "entry >30 off %", "{:.1f}"),
            ("phase_pooled", "phase_ms", "{:.1f}"),
            ("noise_pooled", "noise_ms", "{:.1f}"),
            ("phase_over_noise", "phase/noise", "{:.2f}"),
            ("slow_sd", "slow-note sd (pooled)", "{:.1f}"),
            ("slow_sd_within", "slow-note sd (within)", "{:.1f}"),
            ("stream_sd_within", "stream-note sd (within)", "{:.1f}"),
            ("degradation", "DEGRADATION (stream/slow)", "{:.2f}"),
            ("slow_bias", "slow-note bias ms", "{:+.1f}"),
            ("alt_mean", "systematic hand offset", "{:+.1f}"),
            ("alt_sd", "random phase term ms", "{:.1f}")]
    cis = boot(keepT, sT, keepK, sK, [k for k, _, _ in keys])
    print("GROUP VALUES, WITH CLUSTER-BOOTSTRAP INTERVALS (over plays)")
    print(f"  {'':<18}{'T':>17}{'K':>17}{'difference':>19}")
    for key, lab, fmt in keys:
        tlo, thi, klo, khi, dlo, dhi = cis[key]
        sep = " SEPARATED" if (dlo > 0 or dhi < 0) else ""
        print(f"  {lab:<18}{fmt.format(mT[key]):>8} [{fmt.format(tlo)},{fmt.format(thi)}]"
              f"{fmt.format(mK[key]):>9} [{fmt.format(klo)},{fmt.format(khi)}]"
              f"{fmt.format(mT[key]-mK[key]):>10} [{fmt.format(dlo)},{fmt.format(dhi)}]{sep}")
    print()

    print("VERDICTS AGAINST THE DECISION RULES OF HYPOTHESIS.md")
    d1 = cis["stream_penalty"]
    print(f"  D1  T's stream penalty <= K's                  -> "
          f"{'MET: A1 refuted' if mT['stream_penalty'] <= mK['stream_penalty'] else 'not met'}"
          f"   (T {mT['stream_penalty']:+.1f} pp, K {mK['stream_penalty']:+.1f} pp)")
    d2 = cis["phase_over_noise"]
    print(f"  D2  T's phase/noise <= K's                     -> "
          f"{'MET: A3 refuted' if mT['phase_over_noise'] <= mK['phase_over_noise'] else 'not met'}"
          f"   (T {mT['phase_over_noise']:.2f}, K {mK['phase_over_noise']:.2f};"
          f" diff [{d2[4]:.2f},{d2[5]:.2f}])")
    d3 = cis["entry_off30"]
    print(f"  D3  T's entry >30% off share <= K's            -> "
          f"{'MET: A4 refuted' if mT['entry_off30'] <= mK['entry_off30'] else 'not met'}"
          f"   (T {mT['entry_off30']:.1f}%, K {mK['entry_off30']:.1f}%;"
          f" diff [{d3[4]:.1f},{d3[5]:.1f}])")
    print(f"  D4  K's own phase/noise >= 0.5                 -> "
          f"{'MET: attribution to tapx fails' if mK['phase_over_noise'] >= 0.5 else 'not met'}"
          f"   (K {mK['phase_over_noise']:.2f}, CI [{d2[2]:.2f},{d2[3]:.2f}])")
    b0, b1 = allT["band"]["160-200"], allT["band"]["70-85"]
    c0, c1 = allK["band"]["160-200"], allK["band"]["70-85"]
    fT = (100 * b0[1] / b0[0] if b0[0] else float("nan")) - (100 * b1[1] / b1[0] if b1[0] else float("nan"))
    fK = (100 * c0[1] / c0[0] if c0[0] else float("nan")) - (100 * c1[1] / c1[0] if c1[0] else float("nan"))
    print(f"  D5  T's curve falls more slowly than K's       -> "
          f"{'MET: A5 supported' if fT < fK else 'not met'}   (fall {fT:.1f} pp vs {fK:.1f} pp)")
    print()
    print("SUMMARY OF THE ONE SKILL-NORMALISED QUANTITY")
    print(f"  slow 300-rate      T {mT['slow_rate']:.1f}%   K {mK['slow_rate']:.1f}%   "
          f"(the matching check)")
    print(f"  slow-note sd       T {mT['slow_sd']:.1f} ms   K {mK['slow_sd']:.1f} ms   "
          f"(pooled, includes between-play bias)")
    print(f"  slow-note sd       T {mT['slow_sd_within']:.1f} ms   K {mK['slow_sd_within']:.1f} ms   "
          f"(within-play, bias removed)")
    print(f"  stream-note sd     T {mT['stream_sd_within']:.1f} ms   K {mK['stream_sd_within']:.1f} ms   "
          f"(within-play)")
    print(f"  DEGRADATION        T {mT['degradation']:.2f}      K {mK['degradation']:.2f}"
          f"      (stream sd / slow sd -- primary outcome)")
    print(f"  interval rms       T {mT['interval_rms']:.1f} ms   K {mK['interval_rms']:.1f} ms")
    print(f"  phase_ms           T {mT['phase_pooled']:.1f} ms   K {mK['phase_pooled']:.1f} ms")
    print(f"  noise_ms           T {mT['noise_pooled']:.1f} ms   K {mK['noise_pooled']:.1f} ms")
    print(f"  phase/noise        T {mT['phase_over_noise']:.2f}   K {mK['phase_over_noise']:.2f}")


if __name__ == "__main__":
    main()
