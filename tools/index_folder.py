"""Index every replay in a folder from its header alone.

Reads only the first few KB of each .osr (the header), so thousands of files
take seconds rather than minutes - the LZMA input data is never touched.

Output: <outdir>/replay_index.csv, one row per replay.

Usage:  python index_folder.py <replaydir> [outdir]
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

import osr_parse

DATE_RE = re.compile(r"\((\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})\)")


def main():
    indir = Path(sys.argv[1] if len(sys.argv) > 1 else "lazerexport/replay")
    outdir = Path(sys.argv[2] if len(sys.argv) > 2 else "out")
    outdir.mkdir(parents=True, exist_ok=True)

    files = sorted(indir.rglob("*.osr"))
    print(f"{len(files)} replay files under {indir}")

    rows = []
    bad = []
    for i, f in enumerate(files):
        try:
            h = osr_parse.load_header(f)
        except Exception as e:
            bad.append((f.name, f"{type(e).__name__}: {e}"))
            continue
        m = DATE_RE.search(f.stem)
        fname_date = (f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
                      if m else "")
        played = h.played_at.strftime("%Y-%m-%d %H:%M:%S") if h.played_at else ""
        total = h.count_300 + h.count_100 + h.count_50 + h.count_miss
        rows.append({
            "file": f.name,
            "path": str(f.relative_to(indir.parent.parent)) if indir.parent.parent in f.parents else str(f),
            "size": f.stat().st_size,
            "mode": h.mode,
            "username": h.username,
            "beatmap_hash": h.beatmap_hash,
            "mods": h.mods_str,
            "mods_raw": h.mods,
            "n300": h.count_300, "n100": h.count_100, "n50": h.count_50,
            "nmiss": h.count_miss, "ngeki": h.count_geki, "nkatu": h.count_katu,
            "objects": total,
            "acc": round(h.acc, 3),
            "score": h.score,
            "max_combo": h.max_combo,
            "perfect": int(h.perfect),
            "played_at": played,
            "fname_date": fname_date,
            "online_id": h.online_id if h.online_id is not None else "",
            "title_from_name": re.sub(r"\s*\(\d{4}-\d{2}-\d{2}[ _][\d-]+\)$", "", f.stem),
        })
        if (i + 1) % 2000 == 0:
            print(f"  ... {i+1}")

    csvp = outdir / "replay_index.csv"
    with csvp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"\nindexed {len(rows)} replays, {len(bad)} unreadable -> {csvp}")

    print("\nmode:", dict(Counter(r["mode"] for r in rows)))
    print("username:", dict(Counter(r["username"] for r in rows).most_common(5)))
    print("mods:", dict(Counter(r["mods"] for r in rows).most_common(10)))
    dates = sorted(r["played_at"][:7] for r in rows if r["played_at"])
    if dates:
        print(f"date range: {dates[0]} .. {dates[-1]}")
        per_month = Counter(d[:7] for d in dates)
        print("plays per month (all):")
        for k in sorted(per_month):
            print(f"   {k}  {per_month[k]:5d}  {'#'*int(per_month[k]/40)}")
    print(f"\ndistinct beatmap hashes: {len(set(r['beatmap_hash'] for r in rows))}")
    print(f"distinct beatmaps with miss<200: "
          f"{len(set(r['beatmap_hash'] for r in rows if r['nmiss'] < 200))}")
    mm = [r["nmiss"] for r in rows]
    mm.sort()
    import numpy as np
    a = np.array(mm)
    print(f"miss counts: median {np.median(a):.0f}  p75 {np.percentile(a,75):.0f}"
          f"  p90 {np.percentile(a,90):.0f}  p99 {np.percentile(a,99):.0f}"
          f"  >=200: {int((a>=200).sum())}")
    if bad:
        print("\nunreadable:")
        for n, why in bad[:10]:
            print(f"   {n}: {why}")


if __name__ == "__main__":
    main()
