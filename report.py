"""osu-replay-report: turn a folder of osu! replays into a readable report.

    python report.py /path/to/replays
    python report.py /path/to/replays --songs "/path/to/osu!/Songs" --out my-report

Reads `.osr` replay files and the `.osu` beatmaps they were played on, measures
how the taps were timed, and writes a report in markdown, a self-contained HTML
page and a machine-readable JSON summary. See README.md for what each metric
means and docs/limitations.md for what a replay cannot show.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import metadata
import render
import replaymetrics as rm
import osr_parse


def log(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="osu-replay-report", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replays", help="folder containing .osr replays")
    ap.add_argument("--out", default="report", help="output folder (default: report)")
    ap.add_argument("--maps", default=None,
                    help="folder of resolved .osu beatmaps (default: <out>/maps)")
    ap.add_argument("--songs", action="append", default=[], metavar="DIR",
                    help="osu! Songs folder, searched to resolve beatmaps without "
                         "downloading. Repeatable.")
    ap.add_argument("--meta-cache", default=metadata.DEFAULT_CACHE,
                    help="beatmap metadata cache file (default: %(default)s)")
    ap.add_argument("--offline", action="store_true",
                    help="never query the mirrors; use caches only")
    ap.add_argument("--formats", default="md,html,json",
                    help="comma-separated: md, html, json (default: all)")
    ap.add_argument("--no-charts", action="store_true", help="skip the charts")
    ap.add_argument("--ceil-300", type=float, default=rm.DEFAULT_CEIL_300,
                    help="300-rate that counts as 'holding' a speed (default: %(default)s)")
    ap.add_argument("--ceil-min-taps", type=int, default=rm.CEIL_MIN_TAPS,
                    help="minimum taps in a bucket to set the ceiling (default: %(default)s)")
    ap.add_argument("--drop-pp", type=float, default=5.0,
                    help="points below the player's slow-note accuracy that count as a "
                         "drop (default: %(default)s)")
    ap.add_argument("--bpm-bucket", type=float, default=20.0,
                    help="effective-BPM bucket width (default: %(default)s)")
    ap.add_argument("--max-replays", type=int, default=0,
                    help="stop after this many replays (0 = all)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    replays = Path(a.replays)
    if not replays.is_dir():
        sys.exit(f"error: no such folder: {replays}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    maps = Path(a.maps) if a.maps else (out / "maps")
    osz = out / "osz"

    cfg = {"ceil_300": a.ceil_300, "ceil_min_taps": a.ceil_min_taps,
           "drop_pp": a.drop_pp, "bpm_bucket": a.bpm_bucket}

    files = sorted(replays.glob("*.osr"))
    if not files:
        sys.exit(f"error: no .osr files in {replays}")
    if a.max_replays:
        files = files[:a.max_replays]
    log(f"{len(files)} replay file(s) in {replays}", a.quiet)

    # ---- star ratings: a replay does not carry one, so look the maps up once
    md5s = []
    for f in files:
        try:
            md5s.append(osr_parse.load_header(str(f)).beatmap_hash)
        except Exception:
            pass
    md5s = list(dict.fromkeys(md5s))
    if a.offline:
        cache = metadata.load_cache(a.meta_cache)
        stars = {h: (cache.get(h) or {}).get("difficulty_rating", float("nan"))
                 for h in md5s}
        log(f"offline: {sum(1 for h in md5s if h in cache)}/{len(md5s)} maps in the "
            f"metadata cache", a.quiet)
    else:
        log(f"resolving metadata for {len(md5s)} beatmap(s)...", a.quiet)
        stars = metadata.stars_for(
            md5s, a.meta_cache,
            progress=(None if a.quiet else
                      (lambda i, n, h, ok: print(f"  {i}/{n}", end="\r", flush=True))))
        if not a.quiet:
            print()
    stars = {k: v for k, v in stars.items() if isinstance(v, (int, float))}

    # ---- analyse
    def prog(i, n, name, ok):
        if not a.quiet:
            print(f"  [{i}/{n}] {name[:60]}", end="\r", flush=True)

    plays, skipped = rm.load_folder(replays, maps, osz, a.songs, stars=stars,
                                   on_progress=prog)
    if not a.quiet:
        print()
    log(f"analysed {len(plays)} replay(s), skipped {len(skipped)}", a.quiet)
    if not plays:
        log("", a.quiet)
        log("Nothing could be analysed. Common causes:", a.quiet)
        log("  * the beatmaps are not reachable — pass --songs pointing at your osu! "
            "Songs folder to resolve them locally", a.quiet)
        log("  * the replays are not osu!standard", a.quiet)
        for f, why in skipped[:10]:
            log(f"    {f}: {why}", a.quiet)
        sys.exit(1)
    if skipped:
        for f, why in skipped:
            log(f"  skipped {f}: {why}", a.quiet)

    agg = rm.aggregate(plays, cfg)

    # ---- charts
    charts = {}
    if not a.no_charts:
        cdir = out / "charts"
        cdir.mkdir(exist_ok=True)
        for name, png in (("speed", render.chart_speed(agg)),
                          ("errors", render.chart_errors(plays, agg)),
                          ("runlen", render.chart_runlen(agg)),
                          ("inputs", render.chart_inputs(agg))):
            if png:
                (cdir / f"{name}.png").write_bytes(png)
                charts[name] = f"charts/{name}.png"
        log(f"wrote {len(charts)} chart(s) to {cdir}", a.quiet)

    # ---- render
    formats = {f.strip().lower() for f in a.formats.split(",") if f.strip()}
    text = render.markdown(agg, plays, charts=charts, cfg=cfg)
    written = []
    if "md" in formats:
        p = out / "report.md"
        p.write_text(text, encoding="utf-8")
        written.append(p)
    if "html" in formats:
        # the HTML embeds the charts, so it stands alone as a single file
        cdir = out / "charts"
        html_charts = {}
        for name in charts:
            fp = cdir / f"{name}.png"
            if fp.exists():
                html_charts[name] = fp.read_bytes()
        p = out / "report.html"
        p.write_text(render.html(text, charts=html_charts), encoding="utf-8")
        written.append(p)
    if "json" in formats:
        p = out / "summary.json"
        p.write_text(json.dumps(
            {"summary": rm.to_summary(agg, cfg),
             "generated": datetime.now().isoformat(timespec="seconds"),
             "replays_dir": str(replays),
             "skipped": [{"file": f, "reason": w} for f, w in skipped]},
            indent=1, default=str), encoding="utf-8")
        written.append(p)

    # ---- console summary
    fp = agg["fingerprint"]
    log("", a.quiet)
    log(f"playstyle: {fp['verdict']}", a.quiet)
    log(f"300-rate {agg['acc_300_rate']:.1f}%   bias {agg['bias']:+.1f} ms   "
        f"scatter {agg['jitter_sd_within']:.1f} ms", a.quiet)
    if agg.get("speed_ceiling"):
        c = agg["speed_ceiling"]
        log(f"held 1/4 streams to {c['lo']:.0f}-{c['hi']:.0f} eff. BPM at "
            f"{c['rate300']:.1f}% ({c['n']} taps)", a.quiet)
    else:
        log("no 1/4-or-denser bucket reached the ceiling threshold with enough taps",
            a.quiet)
    if agg.get("speed_knee"):
        k = agg["speed_knee"]
        log(f"accuracy starts to drop at {k['lo']:.0f}-{k['hi']:.0f} eff. BPM", a.quiet)
    log("", a.quiet)
    for p in written:
        log(f"wrote {p}", a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
