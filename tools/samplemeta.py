"""Attach beatmap metadata to each replay file in a folder.

Reads only replay headers, matches the beatmap MD5 against out/map_meta.jsonl,
and returns {filename: {hash, date, sr, mods, diffname, objects}}.
"""
from __future__ import annotations

import json
from pathlib import Path

import osr_parse


def load(folder="sample", meta_path="out/map_meta.jsonl"):
    meta = {}
    p = Path(meta_path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                meta[r["hash"]] = r
            except Exception:
                pass
    out = {}
    for f in sorted(Path(folder).glob("*.osr")):
        try:
            h = osr_parse.load_header(f)
        except Exception:
            continue
        m = meta.get(h.beatmap_hash)
        if m is None:
            # not in our cache (someone else's replay) - ask the mirror once
            import fetch_map
            d = fetch_map.lookup_md5(h.beatmap_hash)
            if d:
                m = {"hash": h.beatmap_hash,
                     "difficulty_rating": d.get("difficulty_rating"),
                     "version": d.get("version"),
                     "beatmapset_id": d.get("beatmapset_id"),
                     "accuracy": d.get("accuracy"), "ar": d.get("ar")}
                meta[h.beatmap_hash] = m
                try:
                    with open(meta_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(m) + "\n")
                except OSError:
                    pass
        if m is None:
            m = {}
        out[f.name] = {
            "hash": h.beatmap_hash,
            "date": h.played_at.strftime("%Y-%m-%d") if h.played_at else "",
            "sr": m.get("difficulty_rating"),
            "mods": h.mods_str,
            "diffname": m.get("version", ""),
            "objects": h.count_300 + h.count_100 + h.count_50 + h.count_miss,
        }
    return out


if __name__ == "__main__":
    import sys
    d = load(sys.argv[1] if len(sys.argv) > 1 else "sample")
    n = sum(1 for v in d.values() if isinstance(v["sr"], (int, float)))
    print(f"{len(d)} files, {n} with a star rating")
