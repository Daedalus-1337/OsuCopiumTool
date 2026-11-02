"""Resolve the beatmap for a replay and save the matching .osu next to it.

A .osr records the MD5 of the beatmap it was played on but not its name, so the
title is taken from the replay's filename (both osu!stable and osu!lazer name
replays after the beatmap) and, failing that, from --query. The file is then
downloaded from a public mirror and the .osu inside is accepted only if its MD5
equals the hash in the replay.

Usage:
    python fetch_map.py replay.osr [outdir] [--query "Camellia Racemization"]
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import osr_parse

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MIRROR_SEARCH = [
    "https://osu.direct/api/v2/search?q={q}&page={p}&amount=50",
    "https://catboy.best/api/v2/search?q={q}&page={p}",
]
MIRROR_DL = [
    "https://osu.direct/api/d/{id}",
    "https://catboy.best/d/{id}n",
    "https://catboy.best/d/{id}",
    "https://api.nerinyan.moe/d/{id}",
    "https://beatconnect.io/b/{id}/",
    "https://chimu.moe/d/{id}",
]


def get(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def query_from_filename(path: Path) -> str:
    """Handles every naming scheme seen so far:

    'PlayerName playing Artist - Title (Mapper) [Difficulty] (2026-01-01_12-00).osr'
    'PlayerName C rank on Artist - Title [Difficulty] (2026-01-01 12-00-00).osr'
    'Camellia - Racemization (Ciyus Miapah) [extermination].osr'
    """
    stem = path.stem
    # BeatmapExporter's own format: "<player> <grade> rank on <artist> - <title> [diff] (date)"
    m = re.search(r"\brank on\s+(.+?)\s*\[[^\]]*\]", stem)
    if m:
        return m.group(1)
    m = re.match(r"^.+?\s+playing\s+(.+?)\s*\(([^()]*)\)\s*\[[^\]]*\]", stem)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.match(r"^(.+?)\s*\(([^()]*)\)\s*\[[^\]]*\]", stem)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.match(r"^(.+?)\s*\[[^\]]*\]", stem)
    if m:
        return m.group(1)
    return stem


def search_all(query):
    sets, seen = [], set()
    for tpl in MIRROR_SEARCH:
        for page in (1, 2):
            url = tpl.format(q=urllib.parse.quote(query), p=page)
            try:
                data = json.loads(get(url).decode("utf-8", "replace"))
            except Exception:
                break
            if not isinstance(data, list) or not data:
                break
            for st in data:
                if not isinstance(st, dict) or st.get("id") in seen:
                    continue
                seen.add(st.get("id"))
                sets.append(st)
    return sets


def find_in(sets, md5):
    for st in sets:
        for b in st.get("beatmaps") or []:
            if b.get("checksum") == md5:
                return st, b
    return None, None


def fetch_set(set_id, dest: Path):
    if dest.exists() and dest.read_bytes()[:2] == b"PK":
        return dest
    for url in MIRROR_DL:
        try:
            blob = get(url.format(id=set_id), timeout=300)
            if blob[:2] == b"PK":
                dest.write_bytes(blob)
                print(f"  downloaded {len(blob):,} bytes from {url}")
                return dest
        except Exception as e:
            print(f"  {url} -> {type(e).__name__}")
    return None


def extract_matching(osz: Path, md5: str, outdir: Path):
    zf = zipfile.ZipFile(io.BytesIO(osz.read_bytes()))
    for name in zf.namelist():
        if not name.lower().endswith(".osu"):
            continue
        content = zf.read(name)
        if hashlib.md5(content).hexdigest() == md5:
            dest = outdir / Path(name).name
            dest.write_bytes(content)
            return dest
    return None


def fallback_queries(q):
    """Broader searches in case the mapper name in the filename is spelled
    differently on the mirror (or is missing)."""
    outs = []
    if " - " in q:
        artist, rest = q.split(" - ", 1)
        words = rest.split()
        for n in (5, 3, 2):
            if len(words) >= n:
                outs.append(f"{artist} - {' '.join(words[:n])}")
    return outs


def scan_local(dirs, md5):
    """Look for the exact .osu in local folders (e.g. an osu! Songs directory).
    Only folders the caller explicitly passes are ever read."""
    for d in dirs or []:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.rglob("*.osu"):
            try:
                if hashlib.md5(p.read_bytes()).hexdigest() == md5:
                    print(f"  found it locally: {p}")
                    return p
            except OSError:
                continue
    return None


def lookup_md5(h, timeout=25):
    """Beatmap metadata straight from the hash. Removes the need to guess a
    title from the filename at all."""
    for tpl in ("https://osu.direct/api/v2/md5/{h}",
                "https://catboy.best/api/v2/md5/{h}"):
        try:
            req = urllib.request.Request(tpl.format(h=h), headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(d, dict) and d.get("beatmapset_id"):
                return d
        except Exception:
            continue
    return None


def resolve(replay_path, outdir=".", query=None, osz_dir=None, local_dirs=None):
    rep = osr_parse.load(replay_path)
    md5 = rep.beatmap_hash
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    osz_dir = Path(osz_dir) if osz_dir else outdir
    osz_dir.mkdir(parents=True, exist_ok=True)
    for existing in outdir.glob("*.osu"):
        if hashlib.md5(existing.read_bytes()).hexdigest() == md5:
            print(f"already have it: {existing}")
            return existing
    local = scan_local(local_dirs, md5)
    if local:
        dest = outdir / local.name
        dest.write_bytes(local.read_bytes())
        print(f"  copied to {dest}")
        return dest

    # best path: ask the mirror what this hash is
    d = lookup_md5(md5)
    if d:
        set_id = d["beatmapset_id"]
        sr = d.get("difficulty_rating")
        print(f"  hash resolves to set {set_id} [{d.get('version', '?')}]"
              f"  {d.get('status', '?')}"
              + (f"  {sr:.2f}*" if isinstance(sr, (int, float)) else ""))
        osz = osz_dir / f"{set_id}.osz"
        if fetch_set(set_id, osz):
            dest = extract_matching(osz, md5, outdir)
            if dest:
                print(f"  saved {dest}")
                return dest
        print("  could not fetch that beatmapset; falling back to a name search")

    q = query or query_from_filename(Path(replay_path))
    print(f"beatmap md5 {md5}")
    tried = [q] + ([] if query else fallback_queries(q))
    sets, st, b = [], None, None
    for t in tried:
        print(f"  searching mirrors for '{t}' ...")
        sets += search_all(t)
        st, b = find_in(sets, md5)
        if st:
            break

    if not st:
        probe = q.lower()
        key = q.split(" - ")[-1].split()[0].lower() if " - " in q else q.split()[0].lower()
        seen_map = [s for s in sets if key and (key in s.get("title", "").lower()
                                                or key in s.get("artist", "").lower())]
        if seen_map:
            print(f"  found the beatmapset ({seen_map[0]['artist']} -"
                  f" {seen_map[0]['title']}, id {seen_map[0]['id']}) but no difficulty of it"
                  f" carries this replay's MD5.")
            print("  That means the map has been edited since the play, so the exact .osu"
                  " version that was played no longer exists on the mirrors.")
            print(f"  If you still have osu! installed, that version is inside your Songs"
                  f" folder - point this tool at it and it will match.")
        else:
            print("  no beatmapset matching that name was found on the mirrors.")
        return None

    print(f"  {st['id']}  {st['artist']} - {st['title']}  [{b['version']}]"
          f"  by {st['creator']}  ({st.get('status')})")
    osz = osz_dir / f"{st['id']} {st['artist']} - {st['title']}.osz"
    if not fetch_set(st["id"], osz):
        print("  every download mirror failed")
        return None
    dest = extract_matching(osz, md5, outdir)
    print(f"  saved {dest}" if dest else "  extracted .osu did not match the hash")
    return dest


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    q = None
    if "--query" in sys.argv:
        q = sys.argv[sys.argv.index("--query") + 1]
    resolve(args[0], args[1] if len(args) > 1 else ".", q)
