"""Fetch beatmap metadata (star rating, OD, AR, BPM...) for every distinct
beatmap referenced by the replay index, keyed by MD5.

Resumable: results are appended to a JSONL cache, so an interrupted run picks
up where it left off. Tries osu.direct first, then catboy.

Usage:  python map_lookup.py [index.csv] [outdir]
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ENDPOINTS = [
    "https://osu.direct/api/v2/md5/{h}",
    "https://catboy.best/api/v2/md5/{h}",
]

FIELDS = ("id", "beatmapset_id", "difficulty_rating", "mode", "status", "version",
          "accuracy", "ar", "cs", "drain", "bpm", "total_length", "max_combo")


def fetch(h, timeout=25):
    for tpl in ENDPOINTS:
        try:
            req = urllib.request.Request(tpl.format(h=h), headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def main():
    idx = Path(sys.argv[1] if len(sys.argv) > 1 else "out/replay_index.csv")
    outdir = Path(sys.argv[2] if len(sys.argv) > 2 else "out")
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "map_meta.jsonl"

    rows = list(csv.DictReader(open(idx, encoding="utf-8")))
    # only beatmaps that could ever be analysed: osu!standard, no relax
    cand = [r for r in rows if int(r["mode"]) == 0 and "RX" not in r["mods"]
            and int(r["nmiss"]) < 200]
    hashes = []
    seen = set()
    for r in cand:
        h = r["beatmap_hash"]
        if h and h not in seen:
            seen.add(h)
            hashes.append(h)
    print(f"{len(rows)} replays -> {len(hashes)} distinct beatmaps to look up")

    have = set()
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            try:
                have.add(json.loads(line)["hash"])
            except Exception:
                pass
        print(f"cache already holds {len(have)}")

    todo = [h for h in hashes if h not in have]
    print(f"{len(todo)} still to fetch\n")

    t0 = time.time()
    fails = []
    with cache.open("a", encoding="utf-8") as fh:
        for i, h in enumerate(todo):
            d = fetch(h)
            if d:
                rec = {"hash": h}
                rec.update({k: d.get(k) for k in FIELDS})
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
            else:
                fails.append(h)
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                rate = (i + 1) / el
                print(f"  {i+1}/{len(todo)}  {rate:.1f}/s  eta {(len(todo)-i-1)/rate/60:.1f} min"
                      f"  fails {len(fails)}")
            time.sleep(0.05)

    # ---- materialise a csv ----
    meta = {}
    for line in cache.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            meta[r["hash"]] = r
        except Exception:
            pass
    outp = outdir / "map_meta.csv"
    with outp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["hash"] + list(FIELDS))
        w.writeheader()
        for h, r in meta.items():
            w.writerow({"hash": h, **{k: r.get(k) for k in FIELDS}})
    print(f"\n{len(meta)} beatmaps in {outp}")
    if fails:
        print(f"{len(fails)} lookups failed (rerun to retry them)")
    sr = [r["difficulty_rating"] for r in meta.values()
          if isinstance(r.get("difficulty_rating"), (int, float))]
    if sr:
        import numpy as np
        a = np.array(sr)
        print(f"star rating of the beatmap pool: median {np.median(a):.2f}"
              f"  p10 {np.percentile(a,10):.2f}  p90 {np.percentile(a,90):.2f}"
              f"  max {a.max():.2f}")
        bands = Counter()
        for x in a:
            bands[f"{int(x)}-{int(x)+1}"] += 1
        for k in sorted(bands, key=lambda s: float(s.split('-')[0])):
            print(f"   {k}*  {bands[k]:5d}")


if __name__ == "__main__":
    main()
