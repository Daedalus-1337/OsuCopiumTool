"""Compare two sets of replays on one set of definitions.

    python tools/exp_compare.py <replays_A> <replays_B>
    python tools/exp_compare.py my_replays/ friend_replays/ --labels me,them

Point it at two folders of `.osr` files. Both sides are measured with exactly the
definitions the report uses — `replaymetrics.py` — so a figure here and the same
figure in `report.py` are the same figure. In particular a *stream* is a note at
1/4 or denser **by the map's own snap divisor**, not by a fixed millisecond
threshold, so two players on maps of different tempo stay on one scale.

Every headline quantity is shown for both sides with the difference between them,
and each difference carries an interval from a cluster bootstrap that resamples
*plays*. Taps inside one play share a map, a session and a clock, so they are not
independent samples; plays are the honest unit. A difference whose interval
excludes zero is marked SEPARATED, and that is the only kind worth reading as a
difference at all.

It also reports each side's playstyle fingerprint, so a reader can see whether the
two sides were even doing the same thing. What it cannot do is tell you whether a
difference is about playstyle: with one player per side, player and style are
completely confounded. A result here is a statement about two people. See
docs/limitations.md.

Implementation note: the pooling and the bootstrap both run off the per-play
sufficient statistics in `replaymetrics.py` (`play_rate_sums`, `play_run_sums`),
and the finalisation goes through that module's own functions rather than
repeating the arithmetic. That is deliberate — the comparison cannot drift from
the report, which is what went wrong when it had its own copy of the metrics.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# tools/ is a subdirectory: put the repository root on the path so the shared
# definitions import however this is invoked
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metadata                      # noqa: E402
import replaymetrics as rm           # noqa: E402

DEFAULT_BOOTSTRAP = 400
RNG_SEED = 20261102


# -------------------------------------------------------------- bootstrapping ---
def pool_run_scalars(sel, run_sums):
    """Pool the numeric parts of the per-play run sums for one resample.

    The list-valued parts (used for medians) are dropped: they cannot be pooled
    by addition, and the few quantities read off them are reported un-bootstrapped.
    """
    tot = {k: 0 for k in rm.RUN_SUM_KEYS}
    by_len = {k: [0, 0] for k in run_sums[0]["by_len"]}
    by_sp = {k: [] for k in run_sums[0]["by_len_spacing"]}
    for i in sel:
        s = run_sums[i]
        for k in rm.RUN_SUM_KEYS:
            tot[k] += s[k]
        for k, v in s["by_len"].items():
            by_len[k][0] += v[0]
            by_len[k][1] += v[1]
        for k, v in s["by_len_spacing"].items():
            by_sp[k].extend(v)
    return tot, by_len, by_sp


def finalise_runs(tot, by_len, by_sp):
    """Run the shared finalisation over pooled sums."""
    synth = dict(tot)
    synth["by_len"] = by_len
    synth["by_len_spacing"] = by_sp
    synth["entry_devs"] = []
    synth["gridvals"] = []
    synth["offsets"] = []
    return rm.pool_run_sums([synth])


def metrics_of(sel, rate_sums, run_sums):
    """Every bootstrapped quantity for one resample of one side."""
    rtot = {k: 0 for k in rate_sums[0]}
    for i in sel:
        s = rate_sums[i]
        for k in rtot:
            rtot[k] += s[k]
    m = rm.pool_rate_sums([rtot])
    tot, by_len, by_sp = pool_run_scalars(sel, run_sums)
    m.update(finalise_runs(tot, by_len, by_sp))
    m["stream_penalty"] = (m["sparse_rate"] - m["dense_rate"]
                           if m["sparse_rate"] == m["sparse_rate"] else float("nan"))
    return m


def bootstrap(rate_a, run_a, rate_b, run_b, keys, n_iter, seed=RNG_SEED):
    """Cluster bootstrap over plays. Returns {key: (ci_a, ci_b, ci_diff)}."""
    import numpy as np

    rng = np.random.default_rng(seed)
    ia, ib = np.arange(len(rate_a)), np.arange(len(rate_b))
    vals = {k: {"a": [], "b": [], "d": []} for k in keys}
    for _ in range(n_iter):
        ma = metrics_of(rng.choice(ia, len(ia), True), rate_a, run_a)
        mb = metrics_of(rng.choice(ib, len(ib), True), rate_b, run_b)
        for k in keys:
            va, vb = ma.get(k, float("nan")), mb.get(k, float("nan"))
            vals[k]["a"].append(va)
            vals[k]["b"].append(vb)
            vals[k]["d"].append(va - vb)

    def ci(v):
        v = np.asarray(v, dtype=float)
        v = v[np.isfinite(v)]
        if not v.size:
            return (float("nan"), float("nan"))
        return (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))

    return {k: (ci(v["a"]), ci(v["b"]), ci(v["d"])) for k, v in vals.items()}


# ------------------------------------------------------------------ reporting ---
# key, label, number format, whether a difference in this has a direction worth
# reading (alternation rate, for instance, is style-dependent and not a score)
METRICS = [
    ("n_taps", "taps analysed", "{:,.0f}", False),
    ("sparse_rate", "300-rate, 1/2 and slower", "{:.1f}", True),
    ("dense_rate", "300-rate, 1/4 and denser", "{:.1f}", True),
    ("stream_penalty", "stream penalty, points", "{:+.1f}", True),
    ("degradation", "precision cost of alternating", "{:.2f}", True),
    ("dense_sd_within", "spread on 1/4+, ms", "{:.1f}", True),
    ("sparse_sd_within", "spread on 1/2 and slower, ms", "{:.1f}", True),
    ("bias", "timing bias, ms (negative = early)", "{:+.1f}", False),
    ("jitter_sd_within", "scatter, own bias removed, ms", "{:.1f}", True),
    ("jitter_sd", "scatter, pooled, ms", "{:.1f}", False),
    ("on_grid15", "in-run intervals on grid", "{:.1f}", True),
    ("off_grid50", "in-run intervals >50% off", "{:.1f}", True),
    ("near_sim_pct", "pairs re-pressed (<25 ms)", "{:.1f}", True),
    ("alternation_pct", "strict alternation (style-dependent)", "{:.1f}", False),
    ("entry_off15", "first interval >15% out", "{:.1f}", True),
    ("entry_off30", "first interval >30% out", "{:.1f}", True),
    ("interval_rms_ms", "interval rms, ms", "{:.1f}", True),
    ("phase_ms", "hand-to-hand separation, ms", "{:.1f}", True),
    ("noise_ms", "own jitter floor, ms", "{:.1f}", True),
    ("phase_over_noise", "separation / jitter", "{:.2f}", True),
]


def fmt(v, spec):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return spec.format(f) if math.isfinite(f) else "-"


def load_side(folder, maps, osz, songs, meta_cache, offline, quiet):
    """Analyse one folder of replays, resolving star ratings as needed."""
    path = Path(folder)
    if not path.is_dir():
        sys.exit(f"error: no such folder: {path}")
    import osr_parse

    md5s = []
    for f in sorted(path.glob("*.osr")):
        try:
            md5s.append(osr_parse.load_header(str(f)).beatmap_hash)
        except Exception:
            pass
    if offline:
        cache = metadata.load_cache(meta_cache)
    else:
        if not quiet:
            print(f"  resolving metadata for {len(set(md5s))} beatmap(s) "
                  f"in {path.name}...", flush=True)
        cache = metadata.enrich(set(md5s), meta_cache)
    stars = {h: (cache.get(h) or {}).get("difficulty_rating") for h in md5s}
    stars = {k: v for k, v in stars.items() if isinstance(v, (int, float))}
    return rm.load_folder(path, maps, osz, songs, stars=stars)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="exp_compare", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replays_a", help="folder of .osr replays for the first set")
    ap.add_argument("replays_b", help="folder of .osr replays for the second set")
    ap.add_argument("--labels", default=None, metavar="A,B",
                    help="names for the two sets (default: the player named in the replays)")
    ap.add_argument("--out", default=None, metavar="FILE",
                    help="also write the comparison as markdown")
    ap.add_argument("--json", default=None, metavar="FILE",
                    help="write the comparison as JSON, for collecting results")
    ap.add_argument("--maps", default="maps",
                    help="folder of resolved .osu beatmaps (default: %(default)s)")
    ap.add_argument("--songs", action="append", default=[], metavar="DIR",
                    help="osu! Songs folder to resolve beatmaps locally (repeatable)")
    ap.add_argument("--meta-cache", default=metadata.DEFAULT_CACHE,
                    help="beatmap metadata cache (default: %(default)s)")
    ap.add_argument("--offline", action="store_true",
                    help="never query the mirrors; use caches only")
    ap.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP,
                    help="resamples per side (default: %(default)s; 0 to skip)")
    ap.add_argument("--min-taps", type=int, default=200,
                    help="warn below this many taps on either side (default: %(default)s)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    label_a = label_b = None
    if a.labels:
        parts = [p.strip() for p in a.labels.split(",")]
        if len(parts) != 2:
            sys.exit("error: --labels takes exactly two names, e.g. --labels me,them")
        label_a, label_b = parts

    maps = Path(a.maps)
    osz = maps.parent / "osz"
    maps.mkdir(parents=True, exist_ok=True)

    sides = []
    for folder, label in ((a.replays_a, label_a), (a.replays_b, label_b)):
        plays, skipped = load_side(folder, maps, osz, a.songs, a.meta_cache,
                                   a.offline, a.quiet)
        if not plays:
            reason = f" ({skipped[0][1]})" if skipped else ""
            sys.exit(f"error: nothing analysable in {folder}{reason}")
        agg = rm.aggregate(plays)
        name = label or (agg.get("usernames") or [Path(folder).name])[0]
        sides.append({"name": name, "folder": str(folder), "agg": agg,
                      "skipped": skipped, "plays": plays,
                      "rate_sums": [rm.play_rate_sums(p) for p in plays],
                      "run_sums": [rm.play_run_sums(p) for p in plays]})
    A, B = sides
    for s in sides:
        s["agg"]["stream_penalty"] = (s["agg"]["sparse_rate"] - s["agg"]["dense_rate"])

    keys = [k for k, _, _, _ in METRICS if k != "n_taps"]
    cis = {}
    if a.bootstrap > 0:
        if not a.quiet:
            print(f"bootstrapping {a.bootstrap} resamples per side, over plays...",
                  flush=True)
        cis = bootstrap(A["rate_sums"], A["run_sums"], B["rate_sums"], B["run_sums"],
                        keys, a.bootstrap)

    out = []
    W = out.append
    W(f"# Replay comparison — {A['name']} vs {B['name']}")
    W("")
    W(f"*{A['agg']['n_plays']:,} replay(s) against {B['agg']['n_plays']:,}, "
      f"{A['agg']['n_taps']:,} taps against {B['agg']['n_taps']:,}. Both sides "
      f"measured with the definitions in `replaymetrics.py`; a stream is a note at "
      f"1/4 or denser by the map's own snap divisor.*")
    W("")
    for lab, s in (("A", A), ("B", B)):
        ag, fp = s["agg"], (s["agg"].get("fingerprint") or {})
        W(f"**{lab} — {s['name']}**  ")
        W(f"{ag['n_plays']:,} replays · {ag['n_taps']:,} taps · "
          f"{fmt(ag.get('star_min'), '{:.2f}')}–{fmt(ag.get('star_max'), '{:.2f}')}★ · "
          f"{fmt(ag.get('played_bpm_min'), '{:.0f}')}–{fmt(ag.get('played_bpm_max'), '{:.0f}')} BPM · "
          f"mods {', '.join(f'{k}×{v}' for k, v in sorted((ag.get('mods') or {}).items()))}")
        W(f"playstyle: **{fp.get('verdict', 'unknown')}** — "
          f"{fmt(fp.get('share_slow'), '{:.0f}')}% of slow notes and "
          f"{fmt(fp.get('share_dense'), '{:.0f}')}% of stream notes from the second input")
        if s["skipped"]:
            W(f"*{len(s['skipped'])} replay(s) skipped, e.g. {s['skipped'][0][1]}*")
        W("")
    if A["agg"]["n_taps"] < a.min_taps or B["agg"]["n_taps"] < a.min_taps:
        W(f"> **Small sample.** One side has fewer than {a.min_taps:,} taps, so most "
          f"differences below will be sampling noise. Treat this as a first look, not "
          f"a result.")
        W("")

    W("## Headline comparison")
    W("")
    W("| quantity | A | B | difference | verdict |" if cis
      else "| quantity | A | B | difference |")
    W("|:--|--:|--:|--:|:--|" if cis else "|:--|--:|--:|--:|")
    separated = []
    for key, label, spec, directional in METRICS:
        va, vb = A["agg"].get(key), B["agg"].get(key)
        diff = ((va - vb) if isinstance(va, (int, float)) and isinstance(vb, (int, float))
                else float("nan"))
        cells = [label, fmt(va, spec), fmt(vb, spec), fmt(diff, spec)]
        if cis:
            lo, hi = cis.get(key, (None, None, (float("nan"), float("nan"))))[2]
            if math.isfinite(lo) and math.isfinite(hi):
                sep = (lo > 0 or hi < 0)
                cells.append("SEPARATED" if sep else "within noise")
                if sep and directional:
                    separated.append((label, diff, lo, hi))
            else:
                cells.append("-")
        W("| " + " | ".join(cells) + " |")
    W("")
    if cis:
        W(f"*A row marked SEPARATED has a 95% interval on the difference that excludes "
          f"zero, from {a.bootstrap} resamples of plays. Everything else is inside "
          f"sampling noise for this much data.*")
        W("")
    if separated:
        W(f"**{len(separated)} measure(s) separate the two sides:** "
          + "; ".join(f"{lab} ({fmt(d, '{:+.1f}')}, interval {fmt(lo, '{:+.1f}')} to "
                      f"{fmt(hi, '{:+.1f}')})" for lab, d, lo, hi in separated) + ".")
        W("")
        W("Separated is not the same as *explained by playstyle*. With one player per "
          "side, player and style cannot be told apart — see `docs/limitations.md`.")
        W("")
    else:
        W("No measure separates the two sides at this sample size.")
        W("")

    W("## Accuracy by spacing band")
    W("")
    W("| band (ms) | A taps | A 300-rate | B taps | B 300-rate | A 2nd input | B 2nd input |")
    W("|:--|--:|--:|--:|--:|--:|--:|")
    def band_key(r):
        return f"{r['lo']:.0f}+" if r["hi"] >= 1e8 else f"{r['lo']:.0f}-{r['hi']:.0f}"
    ba = {band_key(r): r for r in A["agg"].get("by_spacing", [])}
    bb = {band_key(r): r for r in B["agg"].get("by_spacing", [])}
    for k in sorted(set(ba) | set(bb), key=lambda s: float(s.split("-")[0].rstrip("+"))):
        ra, rb = ba.get(k), bb.get(k)
        g = lambda r, f: fmt(r.get(f), "{:.1f}") if r else "-"      # noqa: E731
        n = lambda r: fmt(r.get("n"), "{:,.0f}") if r else "-"      # noqa: E731
        W(f"| {k} | {n(ra)} | {g(ra, 'rate300')} | {n(rb)} | {g(rb, 'rate300')} | "
          f"{g(ra, 'second_input_share')} | {g(rb, 'second_input_share')} |")
    W("")

    W("## Accuracy by unbroken stream-run length")
    W("")
    W("| run length | A taps | A 300-rate | A spacing | B taps | B 300-rate | B spacing |")
    W("|:--|--:|--:|--:|--:|--:|--:|")
    ra_all = {r["bucket"]: r for r in A["agg"].get("by_run_len", [])}
    rb_all = {r["bucket"]: r for r in B["agg"].get("by_run_len", [])}
    for _, _, k in rm.RUN_LENGTH_BUCKETS:
        ra, rb = ra_all.get(k), rb_all.get(k)
        na = ra.get("n", 0) if ra else 0
        nb = rb.get("n", 0) if rb else 0
        if max(na, nb) < 10:
            continue
        g = lambda r, f: fmt(r.get(f), "{:.1f}") if r else "-"      # noqa: E731
        n = lambda r: fmt(r.get("n"), "{:,.0f}") if r else "-"      # noqa: E731
        s = lambda r: fmt(r.get("spacing_ms"), "{:.0f} ms") if r else "-"   # noqa: E731
        W(f"| {k} notes | {n(ra)} | {g(ra, 'rate300')} | {s(ra)} | "
          f"{n(rb)} | {g(rb, 'rate300')} | {s(rb)} |")
    W("")

    W("---")
    W("")
    W("Definitions: `docs/metrics.md`. What a replay cannot show: "
      "`docs/limitations.md`. Regenerate with `--bootstrap` for more or fewer "
      "resamples.")
    W("")

    text = "\n".join(out)
    print(text, end="")
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"\nwrote {a.out}")
    if a.json:
        empty = (None, None, None)
        payload = {
            "a": {"name": A["name"], "folder": A["folder"],
                  "n_plays": A["agg"]["n_plays"], "n_taps": A["agg"]["n_taps"],
                  "playstyle": (A["agg"].get("fingerprint") or {}).get("verdict")},
            "b": {"name": B["name"], "folder": B["folder"],
                  "n_plays": B["agg"]["n_plays"], "n_taps": B["agg"]["n_taps"],
                  "playstyle": (B["agg"].get("fingerprint") or {}).get("verdict")},
            "bootstrap": a.bootstrap,
            "metrics": {
                k: {
                    "a": A["agg"].get(k), "b": B["agg"].get(k),
                    "diff": ((A["agg"].get(k) - B["agg"].get(k))
                             if isinstance(A["agg"].get(k), (int, float))
                             and isinstance(B["agg"].get(k), (int, float)) else None),
                    "ci_a": (cis.get(k) or empty)[0] if cis else None,
                    "ci_b": (cis.get(k) or empty)[1] if cis else None,
                    "ci_diff": (cis.get(k) or empty)[2] if cis else None,
                    "separated": (bool((cis.get(k) or (None, None, (float("nan"),) * 2))[2][0] > 0
                                       or (cis.get(k) or (None, None, (float("nan"),) * 2))[2][1] < 0)
                                  if cis else None),
                } for k, _, _, _ in METRICS
            },
        }
        Path(a.json).write_text(json.dumps(payload, indent=1, default=str),
                                encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
