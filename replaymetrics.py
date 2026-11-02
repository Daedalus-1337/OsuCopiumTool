"""Canonical per-play analysis for osu! replays.

One implementation of every metric, used by the report generator and available as
a library. The judgement model and clock calibration come from `analyze.py`,
unchanged; everything above them is defined here.

Conventions
-----------
* **Wall-clock milliseconds.** Replays of modded plays (DT/NC/HT) are put on the
  real-time axis, so 150 ms of note spacing means 150 ms to the player whatever
  mods were used. Hit windows are divided by the same factor.
* **Snap divisor.** Every note is labelled by how much of a beat it was spaced
  from the previous note: 1 = one note per beat (1/1), 2 = 1/2, 4 = 1/4, and so
  on. This is read from the beatmap's own timing points, so it needs no
  assumption about the map and no threshold calibrated on any player.
* **Effective BPM.** The note rate expressed in the convention players use: the
  BPM the map would need for the same note rate as 1/4 notes. A 1/8 stream on a
  96 BPM map is 192 effective BPM. It equals wall BPM x divisor / 4, and the note
  rate in notes per second is effective BPM / 15.
* **Stream run.** A maximal sequence of consecutive notes at divisor >= 4, i.e.
  notes a quarter of a beat apart or denser. This is map-relative: 1/4 on a slow
  map and 1/8 on a half-speed one are both runs.

Nothing here needs to know which physical device produced a tap. The client folds
keyboard keys and mouse buttons into the same two actions, so a replay can say
*that* two inputs were used and which one fired, not whether either is a pen.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import analyze
import fetch_map
import osr_parse
import osu_beatmap

COMMON_DIVISORS = (1, 2, 3, 4, 6, 8, 12, 16)
OFFGRID_TOL = 0.15        # spacing further than this from a divisor is unlabelled
RUN_MIN_DIVISOR = 4       # 1/4 or denser counts as a run
NEAR_SIM_MS = 25.0        # press interval this short = a pair re-pressed
STREAM_DIVISOR = 4        # "dense" for the precision contrast
SPARSE_DIVISOR = 2        # "sparse" for the precision contrast
DEFAULT_CEIL_300 = 90.0   # 300-rate counted as "held" at the stream ceiling
CEIL_MIN_TAPS = 100      # minimum taps in a bucket before it can set the ceiling

BUTTON_LABEL = {1: "input 1", 2: "input 2", 4: "input 3", 8: "input 4"}


# ----------------------------------------------------------------- beatmaps ---
def beat_length_at(bm, t_map: float) -> float | None:
    """Beat length in map-clock ms at a map-clock time.

    `osu_beatmap.Beatmap.base_beat_length` returns a 500 ms default for times
    before the map's first uninherited timing point, which is common: maps often
    start their timing at the first note rather than at zero. This returns the
    earliest uninherited beat length instead, and None if the map has none.
    """
    ups = sorted((tp for tp in bm.timing_points
                  if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not ups:
        return None
    chosen = ups[0].beat_length
    for tp in ups:
        if tp.time <= t_map:
            chosen = tp.beat_length
        else:
            break
    return chosen


def label_divisor(beat_len: float, gap_map: float):
    """(divisor, notes per beat) for a note spacing, or (None, ratio) if off-grid."""
    if not beat_len or beat_len <= 0 or gap_map <= 0:
        return None, float("nan")
    ratio = beat_len / gap_map
    n = min(COMMON_DIVISORS, key=lambda c: abs(ratio - c))
    if abs(ratio - n) > OFFGRID_TOL * n:
        return None, ratio
    return n, ratio


def map_index(maps_dir: Path) -> dict:
    """{md5: path} for every .osu in a folder, read once."""
    idx = {}
    for p in Path(maps_dir).glob("*.osu"):
        try:
            idx.setdefault(hashlib.md5(p.read_bytes()).hexdigest(), p)
        except OSError:
            continue
    return idx


def resolve_beatmap(replay_path: Path, md5: str, idx: dict, maps_dir: Path,
                    osz_dir: Path, songs=None):
    """Local cache first, then the public mirrors. Returns (path, note)."""
    if md5 in idx:
        return idx[md5], "cached"
    dest = fetch_map.resolve(str(replay_path), str(maps_dir), osz_dir=str(osz_dir),
                             local_dirs=songs or [])
    if dest:
        idx[md5] = Path(dest)
        return Path(dest), "downloaded"
    return None, "unresolved"


# --------------------------------------------------------------------- play ---
@dataclass
class Play:
    """One replay, with per-note arrays in wall-clock milliseconds."""
    file: str
    username: str = ""
    artist: str = ""
    title: str = ""
    version: str = ""
    creator: str = ""
    beatmap_id: int = 0
    beatmapset_id: int = 0
    mods: int = 0
    mods_str: str = ""
    played_at: str = ""
    stars: float = float("nan")
    od: float = 0.0
    ar: float = 0.0
    cs: float = 0.0
    hp: float = 0.0
    map_bpm_min: float = float("nan")
    map_bpm_max: float = float("nan")
    map_bpm_used: float = float("nan")   # median BPM at the notes actually played
    n_objects: int = 0
    acc_reported: float = float("nan")
    acc_model: float = float("nan")
    count_residual: float = float("nan")
    clock_delta: float = 0.0
    w300: float = 0.0
    w100: float = 0.0
    w50: float = 0.0
    speed: float = 1.0
    # per-note arrays, matched taps only
    err: np.ndarray = field(default_factory=lambda: np.zeros(0))
    button: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    gap: np.ndarray = field(default_factory=lambda: np.zeros(0))
    divisor: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    eff_bpm: np.ndarray = field(default_factory=lambda: np.zeros(0))
    run_id: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    run_len: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    run_pos: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))

    @property
    def url(self) -> str:
        if self.beatmap_id:
            return f"https://osu.ppy.sh/b/{self.beatmap_id}"
        if self.beatmapset_id:
            return f"https://osu.ppy.sh/s/{self.beatmapset_id}"
        return ""

    @property
    def set_url(self) -> str:
        if self.beatmapset_id and self.beatmap_id:
            return f"https://osu.ppy.sh/beatmapsets/{self.beatmapset_id}#osu/{self.beatmap_id}"
        return ""

    @property
    def label(self) -> str:
        v = f" [{self.version}]" if self.version else ""
        return f"{self.artist} - {self.title}{v}"


def analyse_replay(osr_path: Path, osu_path: Path, stars: float = float("nan")) -> Play | None:
    """Judge one replay against its beatmap and return a populated Play."""
    rep = osr_parse.load(str(osr_path))
    if rep.mode != 0:
        raise ValueError(f"mode {rep.mode} is not osu!standard")
    bm = osu_beatmap.parse(osu_path)
    taps = [o for o in bm.objects if not o.is_spinner]
    if not taps:
        return None
    raw_t = np.array([o.time for o in taps])
    presses = analyze.press_events(rep)
    game = {"300": rep.count_300, "100": rep.count_100, "50": rep.count_50,
            "miss": rep.count_miss}

    rate, delta, residual, modc = analyze.calibrate(
        raw_t, presses, bm.od, rep.mods, game, len(bm.objects) - len(taps))
    nt_map = raw_t / rate
    err, btn, hit, pt, _stray, _st = analyze.judge(nt_map, presses, delta=delta)
    if not hit.any():
        return None

    speed = analyze.speed_from_mods(rep.mods)
    if abs(speed - 1.0) > 1e-9:
        err = err / speed
        pt = pt / speed
        analyze.W300 /= speed
        analyze.W100 /= speed
        analyze.W50 /= speed

    # wall-clock spacing between consecutive notes, and its snap label
    gap_map = np.diff(nt_map, prepend=nt_map[0])
    gap_wall = gap_map / speed
    nb = len(nt_map)
    divisor = np.zeros(nb, dtype=int)
    eff = np.zeros(nb)
    for i in range(1, nb):
        bl = beat_length_at(bm, nt_map[i])
        if bl is None:
            continue
        n, _ = label_divisor(bl, gap_map[i])
        if n is None:
            continue
        divisor[i] = n
        eff[i] = (60000.0 / bl) * speed * n / 4.0

    # stream runs: consecutive notes at divisor >= RUN_MIN_DIVISOR
    run_id = np.zeros(nb, dtype=int)
    run_len = np.zeros(nb, dtype=int)
    run_pos = np.zeros(nb, dtype=int)
    rid = 0
    i = 1
    while i < nb:
        if divisor[i] >= RUN_MIN_DIVISOR and hit[i] and hit[i - 1]:
            j = i
            while (j + 1 < nb and divisor[j + 1] >= RUN_MIN_DIVISOR
                   and hit[j + 1]):
                j += 1
            length = j - i + 2                      # includes the entry note i-1
            for k in range(i - 1, j + 1):
                run_id[k] = rid
                run_len[k] = length
                run_pos[k] = k - (i - 1)
            rid += 1
            i = j + 1
        else:
            i += 1

    bpm_lo, bpm_hi = bm.bpm_range()
    m = hit
    played_bpm = []
    for i in range(nb):
        if not hit[i]:
            continue
        bl = beat_length_at(bm, nt_map[i])
        if bl and bl > 0:
            # wall-clock BPM: a 128 BPM map played with DT is played at 192
            played_bpm.append(60000.0 / bl * speed)
    return Play(
        file=osr_path.name, username=rep.username, artist=bm.artist, title=bm.title,
        version=bm.version, creator=bm.creator, beatmap_id=bm.beatmap_id,
        beatmapset_id=bm.beatmapset_id, mods=rep.mods, mods_str=rep.mods_str,
        played_at=rep.played_at.strftime("%Y-%m-%d") if rep.played_at else "",
        stars=stars, od=bm.od, ar=bm.ar, cs=bm.cs, hp=bm.hp,
        map_bpm_min=bpm_lo or float("nan"), map_bpm_max=bpm_hi or float("nan"),
        map_bpm_used=float(np.median(played_bpm)) if played_bpm else float("nan"),
        n_objects=len(bm.objects), acc_reported=rep.acc, acc_model=analyze.acc_of(modc),
        count_residual=residual, clock_delta=delta, w300=analyze.W300,
        w100=analyze.W100, w50=analyze.W50, speed=speed,
        err=err[m], button=btn[m], gap=gap_wall[m], divisor=divisor[m],
        eff_bpm=eff[m], run_id=run_id[m], run_len=run_len[m], run_pos=run_pos[m])


def load_folder(replays_dir, maps_dir, osz_dir=None, songs=None, stars=None,
                on_progress=None):
    """Analyse every .osr in a folder. Returns (plays, skipped).

    skipped is a list of (filename, reason). `stars` optionally maps beatmap md5
    to a current star rating, since a replay does not carry one.
    """
    replays_dir = Path(replays_dir)
    maps_dir = Path(maps_dir)
    osz_dir = Path(osz_dir or (maps_dir.parent / "osz"))
    maps_dir.mkdir(parents=True, exist_ok=True)
    osz_dir.mkdir(parents=True, exist_ok=True)
    idx = map_index(maps_dir)
    plays, skipped = [], []
    files = sorted(replays_dir.glob("*.osr"))
    for k, f in enumerate(files, 1):
        try:
            rep = osr_parse.load_header(str(f))
        except Exception as e:                       # unreadable file
            skipped.append((f.name, f"unreadable: {type(e).__name__}"))
            continue
        if rep.mode != 0:
            skipped.append((f.name, f"mode {rep.mode} is not osu!standard"))
            continue
        osu, _how = resolve_beatmap(f, rep.beatmap_hash, idx, maps_dir, osz_dir, songs)
        if osu is None:
            skipped.append((f.name, "beatmap not resolvable"))
            continue
        st = float("nan")
        if stars:
            st = stars.get(rep.beatmap_hash, float("nan"))
        try:
            p = analyse_replay(f, osu, stars=st)
        except Exception as e:
            skipped.append((f.name, f"{type(e).__name__}: {e}"))
            continue
        if p is None:
            skipped.append((f.name, "no matched taps"))
            continue
        plays.append(p)
        if on_progress:
            on_progress(k, len(files), f.name, len(plays))
    return plays, skipped


# ---------------------------------------------------------------- aggregates ---
def wilson(k: int, n: int, z: float = 1.96):
    """Wilson interval for a proportion. Taps inside one play are not
    independent, so this understates the true interval; the report says where
    that matters."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * max(0.0, c - h), 100 * min(1.0, c + h))


def _rate(mask, ok) -> tuple:
    n = int(mask.sum())
    if not n:
        return 0, float("nan"), (float("nan"), float("nan"))
    k = int((mask & ok).sum())
    return n, 100 * k / n, wilson(k, n)


def _sd(vals):
    return float(np.std(vals)) if len(vals) else float("nan")


def aggregate(plays, cfg=None):
    """Every pooled number the report needs, from a list of Play."""
    cfg = cfg or {}
    ceil_300 = cfg.get("ceil_300", DEFAULT_CEIL_300)
    ceil_min = cfg.get("ceil_min_taps", CEIL_MIN_TAPS)
    drop_pp = cfg.get("drop_pp", 5.0)

    if not plays:
        return {}
    err = np.concatenate([p.err for p in plays])
    btn = np.concatenate([p.button for p in plays])
    gap = np.concatenate([p.gap for p in plays])
    div = np.concatenate([p.divisor for p in plays])
    eff = np.concatenate([p.eff_bpm for p in plays])
    # one window per tap, from its own play
    w300 = np.concatenate([np.full(len(p.err), p.w300) for p in plays])
    ok = np.abs(err) <= w300
    dense = (div >= STREAM_DIVISOR)
    sparse = (div > 0) & (div <= SPARSE_DIVISOR)

    out = {"n_plays": len(plays), "n_taps": int(len(err))}
    out["usernames"] = sorted({p.username for p in plays if p.username})
    dates = sorted(p.played_at for p in plays if p.played_at)
    out["date_first"], out["date_last"] = (dates[0], dates[-1]) if dates else ("", "")
    stars = [p.stars for p in plays if p.stars == p.stars]
    out["star_min"] = min(stars) if stars else float("nan")
    out["star_max"] = max(stars) if stars else float("nan")
    out["star_median"] = float(np.median(stars)) if stars else float("nan")
    bpm_lo = [p.map_bpm_min for p in plays if p.map_bpm_min == p.map_bpm_min]
    bpm_hi = [p.map_bpm_max for p in plays if p.map_bpm_max == p.map_bpm_max]
    out["map_bpm_min"] = min(bpm_lo) if bpm_lo else float("nan")
    out["map_bpm_max"] = max(bpm_hi) if bpm_hi else float("nan")
    used = [p.map_bpm_used for p in plays if p.map_bpm_used == p.map_bpm_used]
    out["played_bpm_min"] = min(used) if used else float("nan")
    out["played_bpm_max"] = max(used) if used else float("nan")
    out["mods"] = {}
    for p in plays:
        out["mods"][p.mods_str] = out["mods"].get(p.mods_str, 0) + 1

    # ---- timing
    n, r, ci = _rate(np.ones(len(err), bool), ok)
    out["acc_300_rate"] = r
    out["acc_300_ci"] = ci
    out["acc_reported"] = float(np.mean([p.acc_reported for p in plays]))
    out["acc_model"] = float(np.mean([p.acc_model for p in plays]))
    out["bias"] = float(err.mean())
    out["median"] = float(np.median(err))
    out["late_pct"] = 100 * float((err > 0).mean())
    out["jitter_sd"] = _sd(err)
    out["w300_median"] = float(np.median(w300))
    out["off_300_pct"] = 100 * float((~ok).mean())
    # pooled sd with each play's own bias removed
    ss = sum(float(((p.err - p.err.mean()) ** 2).sum()) for p in plays)
    out["jitter_sd_within"] = math.sqrt(ss / len(err)) if len(err) else float("nan")
    # what centring each play's errors on zero would be worth
    gain = []
    for p in plays:
        if len(p.err) < 20:
            continue
        a = 100 * float((np.abs(p.err) <= p.w300).mean())
        b = 100 * float((np.abs(p.err - np.median(p.err)) <= p.w300).mean())
        gain.append(b - a)
    out["bias_correction_gain"] = float(np.mean(gain)) if gain else float("nan")

    # ---- precision contrast: dense (1/4+) against sparse (1/2 and slower)
    def within_sd(mask_of):
        ss, nn = 0.0, 0
        for p in plays:
            s = mask_of(p)
            if s.sum() > 1:
                ss += float(((p.err[s] - p.err[s].mean()) ** 2).sum()); nn += int(s.sum())
        return math.sqrt(ss / nn) if nn else float("nan"), nn
    sd_dense, n_dense = within_sd(lambda p: p.divisor >= STREAM_DIVISOR)
    sd_sparse, n_sparse = within_sd(lambda p: (p.divisor > 0) & (p.divisor <= SPARSE_DIVISOR))
    out["dense_sd_within"], out["dense_sd_n"] = sd_dense, n_dense
    out["sparse_sd_within"], out["sparse_sd_n"] = sd_sparse, n_sparse
    out["degradation"] = (sd_dense / sd_sparse) if sd_sparse else float("nan")

    # ---- accuracy by snap divisor
    out["by_divisor"] = []
    for d in COMMON_DIVISORS:
        m = (div == d)
        n, rate, ci = _rate(m, ok)
        if not n:
            continue
        out["by_divisor"].append({
            "divisor": d, "label": f"1/{d}" if d > 1 else "1/1", "n": n, "rate300": rate,
            "ci": ci, "second_input_share": 100 * float((btn[m] == 2).mean()),
            "eff_bpm_median": float(np.median(eff[m])) if (m & (eff > 0)).any() else float("nan"),
            "inputs": sorted(int(x) for x in np.unique(btn[m])),
        })

    # ---- accuracy by effective BPM
    # Two curves. `by_bpm` uses every tap and shows the pattern mix the player
    # actually met. `by_bpm_stream` uses only notes at 1/4 or denser, which is
    # what a stream ceiling means. The ceiling and the knee are read off the
    # restricted curve, against the player's own non-alternating notes as the
    # reference level, so they say how fast he can go before his accuracy drops
    # relative to how he plays everything else.
    out["by_bpm"], out["by_bpm_stream"] = [], []
    dbg = _rate(dense, ok)
    sbg = _rate(sparse, ok)
    out["dense_rate"], out["dense_rate_ci"], out["dense_n"] = dbg[1], dbg[2], dbg[0]
    out["sparse_rate"], out["sparse_rate_ci"], out["sparse_n"] = sbg[1], sbg[2], sbg[0]
    out["speed_plateau"] = sbg[1]
    out["speed_ceiling"] = None
    out["speed_knee"] = None
    out["speed_held_to_baseline"] = None
    width = cfg.get("bpm_bucket", 20.0)
    if (eff > 0).any():
        edges = np.arange(90.0, 420.0 + width, width)
        for a, b in zip(edges[:-1], edges[1:]):
            m = (eff >= a) & (eff < b)
            n, rate, ci = _rate(m, ok)
            if n >= 10:
                out["by_bpm"].append({
                    "lo": float(a), "hi": float(b), "n": n, "rate300": rate, "ci": ci,
                    "divisor_mix": {int(d): int(((div == d) & m).sum())
                                    for d in np.unique(div[m]) if d > 0}})
            ms = m & dense
            n2, rate2, ci2 = _rate(ms, ok)
            if n2 >= 10:
                out["by_bpm_stream"].append({
                    "lo": float(a), "hi": float(b), "n": n2, "rate300": rate2,
                    "ci": ci2,
                    "divisor_mix": {int(d): int(((div == d) & ms).sum())
                                    for d in np.unique(div[ms]) if d > 0}})
        elig = [r for r in out["by_bpm_stream"] if r["n"] >= ceil_min]
        # ceiling: the fastest bucket reached without having fallen below the
        # absolute threshold, counting only the contiguous climb from the
        # slowest eligible bucket
        ceiling = None
        for r in elig:
            if r["rate300"] >= ceil_300:
                ceiling = r
            else:
                break
        out["speed_ceiling"] = ceiling
        # the fastest speed with a substantial sample, whatever the accuracy is:
        # a single threshold answers "how fast did you hold the bar" but hides
        # what the player actually reaches at the top end
        out["speed_fastest"] = elig[-1] if elig else None
        # knee: the slowest bucket that has dropped drop_pp below the player's
        # own baseline accuracy on non-alternating notes
        for r in elig:
            if r["rate300"] < out["speed_plateau"] - drop_pp:
                out["speed_knee"] = r
                break
        held = None
        for r in elig:
            if r["rate300"] >= out["speed_plateau"] - drop_pp:
                held = r
            else:
                break
        out["speed_held_to_baseline"] = held

    # ---- playstyle fingerprint
    out["fingerprint"] = fingerprint(plays, btn, div, gap)

    # ---- runs, alternation and phase
    runs = run_stats(plays, cfg)
    out.update(runs)

    # ---- per input
    out["inputs"] = []
    for bit in sorted(int(x) for x in np.unique(btn)):
        m = (btn == bit)
        n, rate, ci = _rate(m, ok)
        out["inputs"].append({
            "bit": bit, "name": BUTTON_LABEL.get(bit, f"bit {bit}"), "n": n,
            "rate300": rate, "ci": ci, "bias": float(err[m].mean()),
            "sd": _sd(err[m]),
            "share": 100 * n / len(err) if len(err) else float("nan"),
            "share_slow": 100 * float((btn[sparse] == bit).mean()) if sparse.any() else float("nan"),
            "share_dense": 100 * float((btn[dense] == bit).mean()) if dense.any() else float("nan"),
        })

    # ---- absolute spacing view, for continuity with earlier work
    out["by_spacing"] = []
    for lo, hi in ((0, 70), (70, 85), (85, 110), (110, 130), (130, 160),
                   (160, 200), (200, 300), (300, 1e9)):
        m = (gap >= lo) & (gap < hi)
        n, rate, ci = _rate(m, ok)
        if n < 10:
            continue
        out["by_spacing"].append({"lo": lo, "hi": hi, "n": n, "rate300": rate, "ci": ci,
                                  "second_input_share": 100 * float((btn[m] == 2).mean())})
    return out


def fingerprint(plays, btn, div, gap):
    """Estimate the playstyle from how the inputs are distributed."""
    res = {"n_inputs": int(len(np.unique(btn))) if len(btn) else 0,
           "inputs": sorted(int(x) for x in np.unique(btn)) if len(btn) else [],
           "share_by_divisor": [], "verdict": "unknown", "detail": ""}
    slow = (div > 0) & (div <= 2)
    dense = (div >= STREAM_DIVISOR)
    second = 2
    if res["n_inputs"] < 2:
        res["verdict"] = "single input"
        res["detail"] = ("Only one input was used. This is a single-tap style; the "
                         "device behind it cannot be recovered from a replay.")
        return res
    for d in COMMON_DIVISORS:
        m = (div == d)
        if m.sum() >= 30:
            res["share_by_divisor"].append(
                (f"1/{d}" if d > 1 else "1/1", 100 * float((btn[m] == second).mean()),
                 int(m.sum())))
    s_slow = 100 * float((btn[slow] == second).mean()) if slow.any() else float("nan")
    s_dense = 100 * float((btn[dense] == second).mean()) if dense.any() else float("nan")
    res["share_slow"], res["share_dense"] = s_slow, s_dense
    if not dense.any():
        res["verdict"] = "unknown"
        res["detail"] = ("No notes at 1/4 or denser were played, so the alternation "
                         "behaviour cannot be judged.")
    elif s_slow < 15 and s_dense > 35:
        res["verdict"] = "alternates on streams only"
        res["detail"] = (
            f"A second input is used for {s_slow:.0f}% of notes at 1/2 and slower but "
            f"{s_dense:.0f}% at 1/4 and denser. One input carries all the slow notes "
            "and the other joins only when the notes outrun it. This is the pattern "
            "of a player tapping with a pen tip or one finger and reaching for a "
            "second key for streams.")
    elif s_slow > 35 and s_dense > 35:
        res["verdict"] = "alternates throughout"
        res["detail"] = (
            f"Both inputs are used about equally at every speed ({s_slow:.0f}% and "
            f"{s_dense:.0f}%). Taps are shared between the two inputs from 1/1 upwards.")
    elif s_dense < 15:
        res["verdict"] = "one input, two available"
        res["detail"] = (
            f"A second input exists but supplies only {s_dense:.0f}% of taps. "
            "Effectively a single-tap style in these replays.")
    else:
        res["verdict"] = "mixed"
        res["detail"] = (f"Second input {s_slow:.0f}% of slow notes and {s_dense:.0f}% "
                         "of dense notes: alternation is partial or inconsistent.")
    return res


def run_stats(plays, cfg=None):
    """Everything measured inside stream runs."""
    out = {}
    same = nint = 0
    dev15 = dev50 = nearsim = 0
    alt_sq_all, com_sq_all, alt_sum, n_alt = 0.0, 0.0, 0.0, 0
    runs = 0
    entry_devs, entry_abs = [], []
    gridvals = []
    by_len = {"2": [0, 0], "3-5": [0, 0], "6-10": [0, 0], "11+": [0, 0]}
    offsets = []
    for p in plays:
        ids_all = [i for i in range(len(p.err)) if p.run_len[i] > 0]
        if not ids_all:
            continue
        for rid in sorted({int(p.run_id[i]) for i in ids_all}):
            idx = [i for i in ids_all if p.run_id[i] == rid]
            if len(idx) < 2:
                continue
            runs += 1
            bucket = ("2" if len(idx) == 2 else "3-5" if len(idx) <= 5
                      else "6-10" if len(idx) <= 10 else "11+")
            for i in idx:
                by_len[bucket][0] += 1
                by_len[bucket][1] += int(abs(p.err[i]) <= p.w300)
            d = np.diff(p.err[idx])
            g = p.gap[idx][1:]
            if len(g):
                gridvals.append(float(np.median(g)))
            nint += len(d)
            dev = d / g
            dev15 += int((np.abs(dev) <= 0.15).sum())
            dev50 += int((np.abs(dev) > 0.50).sum())
            nearsim += int(((g + d) < NEAR_SIM_MS).sum())
            b = p.button[idx]
            same += int((b[1:] == b[:-1]).sum())
            # entry: the first interval of the run
            f, fg = p.err[idx[1]] - p.err[idx[0]], p.gap[idx[1]]
            if fg > 0:
                entry_devs.append(f / fg)
                entry_abs.append(abs(f / fg))
            # alternating triples for the phase decomposition
            for j in range(len(idx) - 2):
                if b[j] == b[j + 1] or b[j + 1] == b[j + 2]:
                    continue
                if abs(g[j] - g[j + 1]) > 0.15 * g[j]:
                    continue
                a = (d[j] - d[j + 1]) / 2.0
                c = (d[j] + d[j + 1]) / 2.0
                alt_sq_all += a * a
                com_sq_all += c * c
                alt_sum += a
                n_alt += 1
            if len(idx) >= 4 and (b == 1).sum() >= 2 and (b == 2).sum() >= 2:
                offsets.append(float(np.median(p.err[idx][b == 2])
                                     - np.median(p.err[idx][b == 1])))
    out["n_runs"] = runs
    out["grid_ms"] = float(np.median(gridvals)) if gridvals else float("nan")
    out["n_run_intervals"] = nint
    out["on_grid15"] = 100 * dev15 / nint if nint else float("nan")
    out["off_grid50"] = 100 * dev50 / nint if nint else float("nan")
    out["near_sim_pct"] = 100 * nearsim / nint if nint else float("nan")
    out["alternation_pct"] = 100 * (nint - same) / nint if nint else float("nan")
    out["entry_off15"] = 100 * float(np.mean([abs(x) > 0.15 for x in entry_devs])) if entry_devs else float("nan")
    out["entry_off30"] = 100 * float(np.mean([abs(x) > 0.30 for x in entry_devs])) if entry_devs else float("nan")
    out["entry_median_pct"] = 100 * float(np.median(entry_devs)) if entry_devs else float("nan")
    out["n_entry"] = len(entry_devs)
    out["by_run_len"] = [{"bucket": k, "n": v[0], "rate300": (100 * v[1] / v[0]) if v[0] else float("nan")}
                         for k, v in by_len.items()]
    if n_alt:
        ra = math.sqrt(alt_sq_all / n_alt)
        rc = math.sqrt(com_sq_all / n_alt)
        am = alt_sum / n_alt
        out["rms_alt"] = ra
        out["rms_com"] = rc
        out["phase_ms"] = math.sqrt(max(0.0, ra * ra - 3 * rc * rc))
        out["noise_ms"] = rc * math.sqrt(2)
        out["phase_over_noise"] = out["phase_ms"] / out["noise_ms"] if out["noise_ms"] else float("nan")
        out["hand_offset_systematic_ms"] = am
        out["phase_random_ms"] = math.sqrt(max(0.0, ra * ra - am * am))
        out["n_alt_pairs"] = n_alt
    if offsets:
        out["hand_offset_median_ms"] = float(np.median(offsets))
        out["n_offset_runs"] = len(offsets)
    return out


# ------------------------------------------------------------------ summary ---
def to_summary(agg, cfg=None):
    """A small machine-readable digest, for posting and for aggregation across
    players. Fixed key set; see docs/posting-results.md."""
    cfg = cfg or {}
    keep = ["n_plays", "n_taps", "usernames", "date_first", "date_last",
            "star_min", "star_max", "star_median", "map_bpm_min", "map_bpm_max",
            "acc_300_rate", "acc_reported", "bias", "jitter_sd_within",
            "bias_correction_gain", "degradation", "dense_sd_within",
            "sparse_sd_within", "dense_rate", "sparse_rate", "dense_n",
            "sparse_n", "on_grid15", "off_grid50", "near_sim_pct",
            "alternation_pct", "entry_off15", "entry_off30", "phase_ms",
            "noise_ms", "phase_over_noise", "hand_offset_median_ms",
            "n_runs", "n_alt_pairs", "mods"]
    s = {k: agg.get(k) for k in keep if k in agg}
    fp = agg.get("fingerprint") or {}
    s["playstyle"] = fp.get("verdict")
    s["inputs"] = fp.get("inputs")
    s["share_second_input_slow"] = fp.get("share_slow")
    s["share_second_input_dense"] = fp.get("share_dense")
    sc = agg.get("speed_ceiling")
    s["speed_ceiling_bpm"] = ((sc["lo"] + sc["hi"]) / 2) if sc else None
    s["speed_ceiling_rate300"] = sc["rate300"] if sc else None
    s["speed_ceiling_taps"] = sc["n"] if sc else None
    sk = agg.get("speed_knee")
    s["speed_knee_bpm"] = ((sk["lo"] + sk["hi"]) / 2) if sk else None
    sh = agg.get("speed_held_to_baseline")
    s["speed_held_to_baseline_bpm"] = ((sh["lo"] + sh["hi"]) / 2) if sh else None
    s["speed_plateau_rate300"] = agg.get("speed_plateau")
    s["tool"] = {"name": "osu-replay-report", "version": "1.0",
                 "ceil_300": cfg.get("ceil_300", DEFAULT_CEIL_300),
                 "ceil_min_taps": cfg.get("ceil_min_taps", CEIL_MIN_TAPS)}
    return s
