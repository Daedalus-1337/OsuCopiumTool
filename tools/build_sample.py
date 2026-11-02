"""Copy the replays chosen by select_sample.py into a working folder.

Writes sample/ and sample_manifest.csv (sample filename -> which set it came
from), so the analysis output can be joined back to 'profile' / 'progression'.

Usage: python build_sample.py
"""
from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path


def main():
    rows = list(csv.DictReader(open("out/sample.csv", encoding="utf-8")))
    dest = sys.argv[1] if len(sys.argv) > 1 else "sample"
    out = Path(dest)
    out.mkdir(exist_ok=True)
    manifest = {}
    for r in rows:
        src = Path(r["path"])
        if not src.is_absolute():
            src = Path(".") / src
        if not src.exists():
            print(f"  MISSING {src}")
            continue
        dst = out / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
        if src.name in manifest:
            tags = manifest[src.name]["set"].split("+")
            if r["set"] not in tags:
                manifest[src.name]["set"] += "+" + r["set"]
            continue
        manifest[src.name] = {"sample_file": src.name, "set": r["set"],
                              "played_at": r["played_at"], "sr": r["sr"],
                              "mods": r["mods"], "beatmap_hash": r["beatmap_hash"],
                              "diffname": r["diffname"]}
    rows_out = list(manifest.values())
    with open("out/sample_manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)
    print(f"copied {len(rows_out)} replays into {out}/")
    print("wrote out/sample_manifest.csv")


if __name__ == "__main__":
    main()
