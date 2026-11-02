"""Dump the raw metrics for a folder of replays, without generating a report.

    python inspect.py <replays_dir> [--maps maps] [--stars cache.json] [--json out.json]

Prints the aggregate tables that the report is built from, and can write the
whole aggregate (and the machine-readable summary) to JSON. Useful for checking
that a replay set resolved correctly before sharing a report.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import metadata
import replaymetrics as rm


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("replays")
    ap.add_argument("--maps", default="maps")
    ap.add_argument("--osz", default=None)
    ap.add_argument("--songs", action="append", default=[],
                    help="osu! Songs folder, to resolve beatmaps locally")
    ap.add_argument("--meta-cache", default=metadata.DEFAULT_CACHE)
    ap.add_argument("--offline", action="store_true",
                    help="never query the mirrors; use the cache only")
    ap.add_argument("--json", default=None)
    ap.add_argument("--playstyle-only", action="store_true")
    a = ap.parse_args(argv)

    replays = Path(a.replays)
    if not replays.is_dir():
        sys.exit(f"no such folder: {replays}")

    from pathlib import Path as _P

    import osr_parse
    md5s = []
    for f in sorted(replays.glob("*.osr")):
        try:
            md5s.append(osr_parse.load_header(str(f)).beatmap_hash)
        except Exception:
            pass
    stars = {}
    if not a.offline and md5s:
        stars = metadata.stars_for(set(md5s), a.meta_cache,
                                   progress=lambda i, n, h, ok: print(
                                       f"  metadata {i}/{n} {'ok' if ok else 'missing'}", end="\r"))
        print()
    else:
        cache = metadata.load_cache(a.meta_cache)
        stars = {h: (cache.get(h) or {}).get("difficulty_rating", float("nan")) for h in md5s}

    plays, skipped = rm.load_folder(replays, a.maps, a.osz, a.songs, stars=stars,
                                    on_progress=lambda i, n, f, k: None)
    print(f"{len(plays)} plays analysed, {len(skipped)} skipped")
    for f, why in skipped:
        print(f"  skipped {f}: {why}")
    if not plays:
        sys.exit("nothing to report")
    agg = rm.aggregate(plays)

    fp = agg["fingerprint"]
    print(f"\nPLAYSTYLE  {fp['verdict']}")
    print(f"  {fp['detail']}")
    if a.playstyle_only:
        return 0

    print(f"\ntaps {agg['n_taps']}  plays {agg['n_plays']}  "
          f"stars {agg['star_min']:.2f}-{agg['star_max']:.2f}  "
          f"map BPM {agg['map_bpm_min']:.0f}-{agg['map_bpm_max']:.0f}")
    print(f"300-rate {agg['acc_300_rate']:.1f}%  bias {agg['bias']:+.1f} ms  "
          f"jitter {agg['jitter_sd_within']:.1f} ms")
    print(f"sparse (1/1,1/2) {agg['sparse_rate']:.1f}% on {agg['sparse_n']} taps   "
          f"dense (1/4+) {agg['dense_rate']:.1f}% on {agg['dense_n']} taps   "
          f"degradation {agg['degradation']:.2f}")

    print("\nBY SNAP DIVISOR")
    for r in agg["by_divisor"]:
        print(f"  {r['label']:<5} n={r['n']:<6} 300={r['rate300']:5.1f}%  "
              f"second input {r['second_input_share']:5.1f}%  "
              f"median {r['eff_bpm_median']:.0f} eff BPM")

    print("\nBY EFFECTIVE BPM, 1/4 AND DENSER ONLY")
    for r in agg["by_bpm_stream"]:
        mix = ",".join(f"1/{k}:{v}" for k, v in sorted(r["divisor_mix"].items()))
        print(f"  {r['lo']:>4.0f}-{r['hi']:<4.0f} n={r['n']:<6} 300={r['rate300']:5.1f}%  [{mix}]")
    sc, sk = agg.get("speed_ceiling"), agg.get("speed_knee")
    print(f"  ceiling {('%.0f-%.0f BPM at %.1f%% on %d taps' % (sc['lo'], sc['hi'], sc['rate300'], sc['n'])) if sc else 'none reached'}")
    print(f"  knee    {('%.0f-%.0f BPM at %.1f%%' % (sk['lo'], sk['hi'], sk['rate300'])) if sk else 'none (holds its level)'}")

    print("\nRUNS AND ALTERNATION")
    for k in ("n_runs", "on_grid15", "off_grid50", "near_sim_pct", "alternation_pct",
              "entry_off15", "entry_off30", "phase_ms", "noise_ms",
              "phase_over_noise", "hand_offset_median_ms"):
        if k in agg:
            v = agg[k]
            print(f"  {k:<24} {v:.2f}" if isinstance(v, float) else f"  {k:<24} {v}")
    print("  by run length:", ", ".join(
        f"{r['bucket']}:{r['rate300']:.1f}%({r['n']})" for r in agg["by_run_len"]))

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"aggregate": agg, "summary": rm.to_summary(agg)}, indent=1, default=str),
            encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
