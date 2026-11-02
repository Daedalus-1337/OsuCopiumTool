"""Beatmap metadata: current star rating and friends, cached on disk.

A replay does not carry a star rating. `fetch_map.lookup_md5` asks the public
mirrors for the beatmap a replay hash belongs to, which is where the star
rating, OD, AR, BPM and the beatmap ID for a URL come from.

Every lookup is cached in one JSON file keyed by beatmap MD5, so a folder of
replays is looked up once and the tool then works offline. A missing cache entry
is not an error: the report simply shows what the `.osu` file itself contained.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_map

DEFAULT_CACHE = "beatmap-metadata.json"


def load_cache(path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def save_cache(cache: dict, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")


def enrich(md5s, cache_path=DEFAULT_CACHE, progress=None) -> dict:
    """Return {md5: metadata}, querying the mirrors only for uncached hashes."""
    cache = load_cache(cache_path)
    missing = [h for h in md5s if h not in cache]
    for i, h in enumerate(missing, 1):
        try:
            d = fetch_map.lookup_md5(h)
        except Exception:
            d = None
        cache[h] = d or {}
        if progress:
            progress(i, len(missing), h, bool(d))
    if missing:
        save_cache(cache, cache_path)
    return cache


def stars_for(md5s, cache_path=DEFAULT_CACHE, progress=None) -> dict:
    """{md5: star rating} for use as the `stars` argument of load_folder."""
    cache = enrich(md5s, cache_path, progress)
    out = {}
    for h in md5s:
        d = cache.get(h) or {}
        sr = d.get("difficulty_rating")
        out[h] = float(sr) if isinstance(sr, (int, float)) else float("nan")
    return out
