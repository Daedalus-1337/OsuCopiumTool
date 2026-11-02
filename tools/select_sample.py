"""Choose which replays to analyse out of the full export.

Filtering happens on the header index alone (no maps needed), then star ratings
are joined from the map lookup. Two different samples are produced, because
they answer different questions:

  PROFILE     a random, deduplicated, date- and difficulty-spread sample. Answers
              "what does my timing look like at the moment". Random so that the
              selection cannot flatter or punish you.

  PROGRESSION the same maps played many times months apart. Holding the map
              constant removes difficulty as an explanation, which makes it a
              far sharper test of "am I actually improving" than any
              cross-sectional sample can be.

Usage: python select_sample.py [--profile 60] [--seed 12345]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def mod_class(mods: str) -> str:
    """Classify by the mods that actually change timing. NF/SD/PF are ignored -
    they do not move the hit windows, so they should not split the groups."""
    if "RX" in mods:
        return "relax"
    if "DT" in mods or "NC" in mods:
        return "DT"
    if "HT" in mods:
        return "HT"
    if "HR" in mods:
        return "HR"
    return "NM"


def load():
    idx = list(csv.DictReader(open("out/replay_index.csv", encoding="utf-8")))
    meta = {}
    p = Path("out/map_meta.jsonl")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                meta[r["hash"]] = r
            except Exception:
                pass
    return idx, meta


def dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def quarter(d):
    return f"{d.year}Q{(d.month - 1)//3 + 1}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--star-min", type=float, default=4.0)
    ap.add_argument("--star-max", type=float, default=7.0)
    ap.add_argument("--stratify", action="store_true",
                    help="equalise coverage across star bands and quarters. Off by "
                         "default: it over-weights rare bands, which is what made the "
                         "first sample look far worse than the player's actual play.")
    ap.add_argument("--min-objects", type=int, default=150,
                    help="skip very short maps")
    ap.add_argument("--per-map", type=int, default=3)
    ap.add_argument("--per-map-per-quarter", type=int, default=1)
    ap.add_argument("--prog-min-plays", type=int, default=5)
    ap.add_argument("--prog-min-days", type=int, default=150)
    ap.add_argument("--prog-maps", type=int, default=12)
    args = ap.parse_args()

    idx, meta = load()
    print(f"index: {len(idx)} replays,   map metadata for {len(meta)} beatmaps")

    steps = [("all replays", idx)]
    cur = [r for r in idx if int(r["mode"]) == 0]
    steps.append(("osu!standard only", cur))
    cur = [r for r in cur if "RX" not in r["mods"]]
    steps.append(("minus relax (auto-tapped, no input data)", cur))
    cur = [r for r in cur if int(r["nmiss"]) < 200]
    steps.append(("minus 200+ miss plays (your rule)", cur))
    cur = [r for r in cur if int(r["objects"]) >= args.min_objects]
    steps.append((f"minus plays under {args.min_objects} notes", cur))
    cur = [r for r in cur if r["beatmap_hash"] in meta]
    steps.append(("maps with difficulty data", cur))

    print()
    prev = len(idx)
    for label, s in steps:
        print(f"  {label:<45}{len(s):>6}  ({len(s)-prev:+d})")
        prev = len(s)

    pool = []
    for r in cur:
        d = dt(r["played_at"])
        if not d:
            continue
        m = meta[r["beatmap_hash"]]
        sr = m.get("difficulty_rating")
        if not isinstance(sr, (int, float)):
            continue
        r["_d"] = d
        r["_q"] = quarter(d)
        r["_sr"] = float(sr)
        r["_modc"] = mod_class(r["mods"])
        r["_title"] = f"{m.get('version', '')}"
        pool.append(r)
    print(f"\nanalysable pool: {len(pool)} replays over "
          f"{len(set(r['beatmap_hash'] for r in pool))} beatmaps")
    if not pool:
        print("nothing to do - has the map lookup finished?")
        return

    sr_vals = [r["_sr"] for r in pool]
    print(f"star range in pool: {min(sr_vals):.2f} .. {max(sr_vals):.2f}")
    print("mod class:", dict(Counter(r["_modc"] for r in pool)))
    print("quarters   :", dict(sorted(Counter(r["_q"] for r in pool).items())))

    # ---- per-map dedup that keeps the date spread ------------------------
    by_map = defaultdict(list)
    for r in pool:
        by_map[r["beatmap_hash"]].append(r)
    dedup = []
    for h, rs in by_map.items():
        rs.sort(key=lambda x: x["_d"])
        per_q = defaultdict(list)
        for r in rs:
            per_q[r["_q"]].append(r)
        keep = []
        for q, lst in sorted(per_q.items()):
            keep.extend(sorted(lst, key=lambda x: x["_d"])[-args.per_map_per_quarter:])
        if len(keep) > args.per_map:
            # spread across the whole history: first, last, and evenly between
            step = (len(keep) - 1) / (args.per_map - 1)
            keep = [keep[round(i * step)] for i in range(args.per_map)]
        dedup.extend(keep)
    print(f"after '<= {args.per_map} per map, <= {args.per_map_per_quarter} per map per quarter':"
          f" {len(dedup)} replays over {len(set(r['beatmap_hash'] for r in dedup))} maps")

    # ---- PROFILE sample -------------------------------------------------
    def band(sr):
        return f"{int(sr * 2) / 2:.1f}"

    in_range = [r for r in dedup if args.star_min <= r["_sr"] < args.star_max]
    print(f"\nin the {args.star_min:.0f}-{args.star_max:.0f} star window: {len(in_range)} replays"
          f" over {len(set(r['beatmap_hash'] for r in in_range))} maps")

    rng = random.Random(args.seed)
    target = min(args.profile, len(in_range))
    if args.stratify:
        cells = defaultdict(list)
        for r in in_range:
            cells[(band(r["_sr"]), r["_q"])].append(r)
        keys = sorted(cells)
        per_cell = max(1, round(target / max(1, len(keys))))
        chosen = []
        for k in keys:
            lst = sorted(cells[k], key=lambda x: x["_d"])
            rng.shuffle(lst)
            chosen.extend(lst[:per_cell])
        rng.shuffle(chosen)
        chosen = sorted(chosen[:target], key=lambda x: x["_d"])
    else:
        # plain random sample: the sample distribution then matches the actual
        # play distribution, which is what "what does my play look like" needs
        chosen = sorted(rng.sample(in_range, target), key=lambda x: x["_d"])

    print(f"\nPROFILE sample: {len(chosen)} replays  (seed {args.seed})")
    print(f"  star range {min(r['_sr'] for r in chosen):.2f}..{max(r['_sr'] for r in chosen):.2f}"
          f"   mods {dict(Counter(r['_modc'] for r in chosen))}")
    print(f"  quarters  {dict(sorted(Counter(r['_q'] for r in chosen).items()))}")

    # ---- PROGRESSION set: maps played many times over a long span -------
    prog = []
    cand = []
    for h, rs in by_map.items():
        if len(rs) < args.prog_min_plays:
            continue
        span = (rs[-1]["_d"] - rs[0]["_d"]).days
        if span < args.prog_min_days:
            continue
        cand.append((len(rs), span, h, rs))
    cand.sort(key=lambda x: (-x[1], -x[0]))
    used_sr = []
    maps_used = 0
    for n, span, h, rs in cand:
        if maps_used >= args.prog_maps:
            break
        sr = rs[0]["_sr"]
        if not (args.star_min <= sr < args.star_max):
            continue
        if any(abs(sr - u) < 0.4 for u in used_sr):
            continue          # avoid twelve maps of the same difficulty
        used_sr.append(sr)
        step = (len(rs) - 1) / min(8, len(rs) - 1) if len(rs) > 2 else 1
        picks = [rs[round(i * step)] for i in range(min(8, len(rs)))]
        picks = sorted({id(p): p for p in picks}.values(), key=lambda x: x["_d"])
        prog.extend(picks)
        maps_used += 1
        print(f"  progression map: {rs[0]['_title'][:30]:<31} {sr:4.2f}* "
              f"{len(rs):3d} plays over {span:3d} days  -> using {len(picks)}")

    print(f"\nPROGRESSION set: {len(prog)} replays across {len(used_sr)} maps")

    outp = Path("out/sample.csv")
    with outp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["set", "file", "path", "played_at", "sr", "mods", "objects",
                    "acc", "nmiss", "beatmap_hash", "diffname"])
        for tag, lst in (("profile", chosen), ("progression", prog)):
            for r in lst:
                w.writerow([tag, r["file"], r["path"], r["played_at"], f"{r['_sr']:.2f}",
                            r["mods"], r["objects"], r["acc"], r["nmiss"],
                            r["beatmap_hash"], r["_title"]])
    print(f"\nwrote {outp}")
    print(f"total replays that would need maps downloaded: "
          f"{len(set([r['beatmap_hash'] for r in chosen] + [r['beatmap_hash'] for r in prog]))} "
          f"beatmaps for {len(chosen) + len(prog)} replays")


if __name__ == "__main__":
    main()
