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
    # aim, per note across the whole map rather than only judged notes
    aim_state: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    aim_err: np.ndarray = field(default_factory=lambda: np.zeros(0))
    aim_dist: np.ndarray = field(default_factory=lambda: np.zeros(0))
    aim_arrival: np.ndarray = field(default_factory=lambda: np.zeros(0))
    aim_speed: np.ndarray = field(default_factory=lambda: np.zeros(0))
    aim_jump: np.ndarray = field(default_factory=lambda: np.zeros(0))
    aim_arrival_clamped: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    aim_free: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    aim_divisor: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    radius_px: float = float('nan')
    press_reasons: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    press_bits: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    aim_btn: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))

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


# --------------------------------------------------------------- the client ---
def presses_with_position(rep):
    """Every button press with the cursor position at that instant.

    Mirrors analyze.press_events, which discards position, and keeps each frame's
    x/y so a press can be judged the way the client routes it: by what the cursor
    is over, not by which note happens to be closest in time.
    """
    out, prev = [], 0
    for f in rep.frames:
        changed = f.keys ^ prev
        for bit in analyze.BUTTON_NAMES:
            if changed & bit and (f.keys & bit):
                out.append((f.t, bit, f.x, f.y))
        prev = f.keys
    out.sort()
    return out


def circle_radius(cs: float) -> float:
    """osu! circle radius in osu!pixels, the unit replay frames and .osu use."""
    return max(4.0, 54.4 - 4.48 * cs)


def frame_arrays(rep):
    """Frame times and positions as arrays, for cursor lookups."""
    ft = np.fromiter((f.t for f in rep.frames), dtype=float, count=len(rep.frames))
    fx = np.fromiter((f.x for f in rep.frames), dtype=float, count=len(rep.frames))
    fy = np.fromiter((f.y for f in rep.frames), dtype=float, count=len(rep.frames))
    return ft, fx, fy


def positional_pair(note_t, note_xy, radius, presses, blocks):
    """Judge presses against notes the way the client does: by position first.

    A press only judges a note whose circle contains the cursor, taking the one
    nearest in time among those under it. A press whose cursor is over nothing
    judges nothing. A press that would judge a circle is refused while an earlier
    circle is still unjudged and the press arrives before that circle's time.

    Returns per-note arrays and a per-press reason code.
    """
    n = len(note_t)
    t = np.asarray(note_t, dtype=float)
    w300, w100, w50 = analyze.W300, analyze.W100, analyze.W50
    err = np.full(n, np.nan)
    btn = np.zeros(n, dtype=int)
    hit = np.zeros(n, dtype=bool)
    dist = np.full(n, np.nan)
    press_t = np.full(n, np.nan)
    press_x = np.full(n, np.nan)
    press_y = np.full(n, np.nan)
    offcircle_press = np.zeros(n, dtype=bool)
    judged = np.zeros(n, dtype=bool)
    press_bits = np.zeros(len(presses), dtype=int)
    reason = np.zeros(len(presses), dtype=int)      # 0 judged, 1 off circle, 2 locked, 3 no note in window

    for k, (pt, bit, px, py) in enumerate(presses):
        press_bits[k] = bit
        lo = int(np.searchsorted(t, pt - w50, side="left"))
        hi = int(np.searchsorted(t, pt + w50, side="right"))
        best, best_gap, saw_off = -1, 1e9, False
        for i in range(lo, hi):
            if judged[i]:
                continue
            d = math.hypot(note_xy[i][0] - px, note_xy[i][1] - py)
            if d <= radius:
                gap = abs(t[i] - pt)
                if gap < best_gap:
                    best_gap, best = gap, i
            else:
                saw_off = True
        if best < 0:
            reason[k] = 1 if saw_off else 3
            if saw_off:
                for i in range(lo, hi):
                    if not judged[i]:
                        offcircle_press[i] = True
            continue
        # note lock: an earlier circle still unjudged refuses a press arriving
        # before that circle's own start time
        locked = False
        for j in range(best - 1, -1, -1):
            if blocks[j] and not judged[j]:
                locked = pt < t[j]
                break
        if locked:
            reason[k] = 2
            continue
        err[best] = pt - t[best]
        btn[best] = bit
        hit[best] = True
        judged[best] = True
        dist[best] = math.hypot(note_xy[best][0] - px, note_xy[best][1] - py)
        press_t[best] = pt
        press_x[best] = px
        press_y[best] = py
        reason[k] = 0
    return (err, btn, hit, dist, press_t, press_x, press_y,
            offcircle_press, reason, press_bits)


def position_at(ft, fx, fy, t):
    """Cursor position at an arbitrary time, interpolated between the two frames
    that bracket it."""
    k = int(np.searchsorted(ft, t, side="right")) - 1
    if k < 0:
        return float(fx[0]), float(fy[0])
    if k >= len(ft) - 1:
        return float(fx[-1]), float(fy[-1])
    span = float(ft[k + 1] - ft[k])
    if span <= 0:
        return float(fx[k]), float(fy[k])
    w = (t - ft[k]) / span
    return (float(fx[k]) + w * (fx[k + 1] - fx[k]),
            float(fy[k]) + w * (fy[k + 1] - fy[k]))


def crossing_time(cx, cy, radius, ft, fx, fy, anchor, back_limit=1000.0):
    """Time the cursor last crossed into the circle before `anchor`.

    Walks back from the anchor while the samples stay inside the circle, so an
    earlier pass through it does not count as an arrival, then solves the crossing
    between the last outside sample and the first inside one. Returns
    (time, clamped); clamped means the cursor had been inside for longer than
    `back_limit` and its entry is earlier than can be measured.
    """
    k = int(np.searchsorted(ft, anchor, side="right")) - 1
    if k < 0:
        return float("nan"), False
    if math.hypot(fx[k] - cx, fy[k] - cy) > radius:
        return float("nan"), False
    j = k
    while j > 0:
        if ft[k] - ft[j - 1] > back_limit:
            return float("nan"), True
        if math.hypot(fx[j - 1] - cx, fy[j - 1] - cy) > radius:
            break
        j -= 1
    if j == 0:
        return float("nan"), True

    d0 = math.hypot(fx[j - 1] - cx, fy[j - 1] - cy)
    d1 = math.hypot(fx[j] - cx, fy[j] - cy)
    frac = (d0 - radius) / (d0 - d1) if d0 > d1 else 0.0
    frac = min(max(frac, 0.0), 1.0)
    return float(ft[j - 1] + frac * (ft[j] - ft[j - 1])), False


def aim_arrays(note_t, note_xy, radius, ft, fx, fy, press_t, speed_half=8.0):
    """Per-note aim quantities.

    arrival  when the cursor last crossed into the circle, relative to the note's
             time. Anchored on the press where the note was judged, on the note's
             own time where it was not.
    speed    cursor speed in osu!px/ms at the press, as a central difference on the
             interpolated path
    jump     distance from the previous note's position, i.e. travel required
    """
    n = len(note_t)
    arrival = np.full(n, np.nan)
    clamped = np.zeros(n, dtype=bool)
    speed_at = np.full(n, np.nan)
    jump = np.full(n, np.nan)
    aim_free = np.zeros(n, dtype=bool)
    for i in range(n):
        if i > 0:
            jump[i] = math.hypot(note_xy[i][0] - note_xy[i - 1][0],
                                 note_xy[i][1] - note_xy[i - 1][1])
            aim_free[i] = jump[i] <= AIM_FREE_PX
        cx, cy = note_xy[i]
        pt = press_t[i]
        anchor = pt if math.isfinite(pt) else note_t[i]
        mu, was_clamped = crossing_time(cx, cy, radius, ft, fx, fy, anchor)
        if math.isfinite(mu):
            arrival[i] = mu - note_t[i]
        clamped[i] = was_clamped
        if math.isfinite(pt):
            x1, y1 = position_at(ft, fx, fy, pt - speed_half)
            x2, y2 = position_at(ft, fx, fy, pt + speed_half)
            speed_at[i] = math.hypot(x2 - x1, y2 - y1) / (2.0 * speed_half)
    return arrival, speed_at, jump, clamped, aim_free


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

    rate, delta, _residual, modc = analyze.calibrate(
        raw_t, presses, bm.od, rep.mods, game, len(bm.objects) - len(taps))
    nt_map = raw_t / rate
    # HardRock mirrors the playfield and enlarges circle size, so the note
    # positions have to be transformed into the space the cursor moved in
    hr = bool(rep.mods & 16)
    cs_eff = min(10.0, bm.cs * 1.3) if hr else bm.cs
    note_xy = [(o.x, (384.0 - o.y) if hr else o.y) for o in taps]
    radius = circle_radius(cs_eff)
    blocks = np.array([o.is_circle for o in taps], dtype=bool)
    (err, btn, hit, dist, press_t, press_x, press_y,
     offcircle, press_reasons, press_bits) = positional_pair(
        nt_map - delta, note_xy, radius, presses_with_position(rep), blocks)
    if not hit.any():
        return None

    speed = analyze.speed_from_mods(rep.mods)
    if abs(speed - 1.0) > 1e-9:
        err = err / speed
        analyze.W300 /= speed
        analyze.W100 /= speed
        analyze.W50 /= speed

    # counts from this model, for validation against the replay's own counts
    judged_err = np.abs(err[hit])
    c300 = int((judged_err <= analyze.W300).sum())
    c100 = int(((judged_err > analyze.W300) & (judged_err <= analyze.W100)).sum())
    c50 = int(((judged_err > analyze.W100) & (judged_err <= analyze.W50)).sum())
    residual = (abs(c300 - game["300"]) + abs(c100 - game["100"])
                + abs(c50 - game["50"]) + abs(int((~hit).sum()) - game["miss"]))

    ft, fx, fy = frame_arrays(rep)
    arrival, speed_at, jump, arrival_clamped, aim_free = aim_arrays(
        nt_map, note_xy, radius, ft, fx, fy, press_t)
    aim_state = np.where(hit, 0, np.where(offcircle, 1, 2)).astype(np.int8)

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
        eff_bpm=eff[m], run_id=run_id[m], run_len=run_len[m], run_pos=run_pos[m],
        aim_state=aim_state, aim_err=err, aim_dist=dist, aim_arrival=arrival,
        aim_speed=speed_at, aim_jump=jump, press_reasons=press_reasons,
        aim_arrival_clamped=arrival_clamped, aim_free=aim_free,
        aim_divisor=divisor, radius_px=radius,
        press_bits=press_bits, aim_btn=btn)


# Mods where the input on the replay is not the player's: relax and autoplay and
# cinema have the client pressing, autopilot has the client aiming. None of them
# can be measured as tapping.
CLIENT_INPUT_MODS = {
    128: "relax: the client supplies the taps",
    2048: "autoplay: the client plays the map",
    8192: "autopilot: the client supplies the aim",
    4194304: "cinema: the client plays the map",
}


def client_input_reason(mods):
    """Reason string if the client produced the input, else None."""
    m = int(mods)
    for bit in sorted(CLIENT_INPUT_MODS):
        if m // bit % 2:
            return CLIENT_INPUT_MODS[bit]
    return None


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
        why = client_input_reason(rep.mods)
        if why:
            skipped.append((f.name, why))
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
    # ---- timing and precision, from the shared per-play sums
    out.update(pool_rate_sums([play_rate_sums(p) for p in plays]))
    out["median"] = float(np.median(err))
    out["w300_median"] = float(np.median(w300))

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
    # Was this tap struck with a different input than the tap before it? Computed
    # inside each play, so the join between two plays is not read as a change.
    alt_here = np.zeros(len(btn), dtype=bool)
    _k = 0
    for _p in plays:
        _b = np.asarray(_p.button, dtype=int)
        if len(_b) > 1:
            alt_here[_k + 1:_k + len(_b)] = _b[1:] != _b[:-1]
        _k += len(_b)

    out["by_bpm"], out["by_bpm_stream"], out["by_bpm_alt"] = [], [], []
    out["speed_plateau"] = out["sparse_rate"]
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
                    "ci": ci2, "alt_pct": 100.0 * float(alt_here[ms].mean()),
                    "divisor_mix": {int(d): int(((div == d) & ms).sum())
                                    for d in np.unique(div[ms]) if d > 0}})
            ma = ms & alt_here
            n3, rate3, ci3 = _rate(ma, ok)
            if n3 >= 10:
                out["by_bpm_alt"].append({
                    "lo": float(a), "hi": float(b), "n": n3, "rate300": rate3,
                    "ci": ci3})
        # the ceiling and the knee are read off alternated stream taps where there
        # are enough of them, because a slow bucket played with one input measures
        # single tapping rather than alternating
        alt_elig = [r for r in out["by_bpm_alt"] if r["n"] >= ceil_min]
        if len(alt_elig) >= 2:
            elig = alt_elig
            out["speed_basis"] = "alternated stream taps"
        else:
            elig = [r for r in out["by_bpm_stream"] if r["n"] >= ceil_min]
            out["speed_basis"] = "1/4-or-denser taps, alternating or not"
        # ceiling: the fastest bucket reached without having fallen below the
        # absolute threshold, counting only the contiguous climb from the
        # slowest eligible bucket
        # the fastest bucket that clears the bar. Accuracy is not required to hold
        # on the way up to it: the bucket where it falls away is reported separately
        # as the knee, so a dip mid-curve is not hidden by a contiguous-climb rule.
        ceiling = None
        for r in elig:
            if r["rate300"] >= ceil_300:
                ceiling = r
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
    # accuracy against position inside a run: does a run degrade as it goes on?
    rpos = np.concatenate([p.run_pos for p in plays])
    rlen = np.concatenate([p.run_len for p in plays])
    in_run = rlen >= 2
    by_pos = []
    for lo, hi, label in ((0, 1, "1-2"), (2, 3, "3-4"), (4, 6, "5-7"),
                          (7, 11, "8-12"), (12, 10 ** 9, "13+")):
        m = np.logical_and(in_run, np.logical_and(rpos >= lo, rpos <= hi))
        if m.any():
            by_pos.append({"band": label, "n": int(m.sum()),
                           "rate300": 100.0 * float(ok[m].mean()),
                           "abs_err": float(np.abs(err[m]).mean())})
    out["by_run_pos"] = by_pos
    out["in_run_taps"] = int(in_run.sum())
    out["in_run_pct"] = 100.0 * float(in_run.mean()) if len(in_run) else float("nan")
    early = [r for r in by_pos if r["band"] in ("1-2", "3-4")]
    deep = [r for r in by_pos if r["band"] in ("5-7", "8-12")]
    if early and deep:
        en = sum(r["n"] for r in early)
        dn = sum(r["n"] for r in deep)
        out["run_pos_early_rate"] = sum(r["rate300"] * r["n"] for r in early) / en
        out["run_pos_early_n"] = en
        out["run_pos_deep_rate"] = sum(r["rate300"] * r["n"] for r in deep) / dn
        out["run_pos_deep_n"] = dn

    runs = run_stats(plays, cfg)
    out.update(runs)
    out.update(pool_aim_sums([play_aim_sums(p) for p in plays]))
    out.update(pool_pattern_sums([play_pattern_sums(p) for p in plays]))

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
        res["verdict"] = "alternates on fast notes only"
        res["detail"] = (
            f"A second input is used for {s_slow:.0f}% of notes at 1/2 and slower but "
            f"{s_dense:.0f}% at 1/4 and denser. One input carries all the slow notes "
            "and the other joins only when the notes outrun it. This is the pattern "
            "of a player tapping with a pen tip or one finger and reaching for a "
            "second key when the notes get fast.")
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


def play_rate_sums(play):
    """Additive per-play accuracy and precision sums.

    Mirrors `play_run_sums`: everything is a count or a sum, so plays pool by
    addition and a bootstrap resample is just a different set of sums.
    """
    w = play.w300
    err = play.err
    ok = np.abs(err) <= w
    dense = play.divisor >= STREAM_DIVISOR
    _b = np.asarray(play.button, dtype=int)
    alt = np.zeros(len(_b), dtype=bool)
    if len(_b) > 1:
        alt[1:] = _b[1:] != _b[:-1]
    alt_dense = np.logical_and(dense, alt)
    sparse = (play.divisor > 0) & (play.divisor <= SPARSE_DIVISOR)
    s = {"n": int(len(err)), "n_ok": int(ok.sum()),
         "sum_e": float(err.sum()), "sum_e2": float((err * err).sum()),
         "late": int((err > 0).sum()), "off300": int((~ok).sum()),
         "n_dense": int(dense.sum()), "dense_ok": int((ok & dense).sum()),
         "n_alt_dense": int(alt_dense.sum()), "alt_dense_ok": int((ok & alt_dense).sum()),
         "alt_dense_ss": 0.0, "n_alt_dense_sd": 0,
         "n_sparse": int(sparse.sum()), "sparse_ok": int((ok & sparse).sum()),
         "dense_ss": 0.0, "n_dense_sd": 0, "sparse_ss": 0.0, "n_sparse_sd": 0,
         "ss_within": 0.0, "gain": 0.0, "gain_n": 0}
    if s["n"] > 1:
        s["ss_within"] = float(((err - err.mean()) ** 2).sum())
    if s["n_dense"] > 1:
        e = err[dense]
        s["dense_ss"] = float(((e - e.mean()) ** 2).sum())
        s["n_dense_sd"] = s["n_dense"]
    if s["n_alt_dense"] > 1:
        e = err[alt_dense]
        s["alt_dense_ss"] = float(((e - e.mean()) ** 2).sum())
        s["n_alt_dense_sd"] = s["n_alt_dense"]
    if s["n_sparse"] > 1:
        e = err[sparse]
        s["sparse_ss"] = float(((e - e.mean()) ** 2).sum())
        s["n_sparse_sd"] = s["n_sparse"]
    if s["n"] >= 20:
        # what centring this play's errors on zero would be worth, in points
        s["gain"] = (100 * float((np.abs(err - np.median(err)) <= w).mean())
                     - 100 * float(ok.mean()))
        s["gain_n"] = 1
    return s


def pool_rate_sums(sums):
    """Finalise pooled accuracy and precision sums into the reported values."""
    if not sums:
        return {}
    tot = {}
    for k in sums[0]:
        tot[k] = sums[0][k]
    for s in sums[1:]:
        for k in tot:
            tot[k] = tot[k] + s[k]

    def pct(num, den):
        return 100.0 * num / den if den else float("nan")

    n = tot["n"]
    out = {
        "n_taps": n,
        "acc_300_rate": pct(tot["n_ok"], n),
        "acc_300_ci": wilson(tot["n_ok"], n),
        "bias": (tot["sum_e"] / n) if n else float("nan"),
        "late_pct": pct(tot["late"], n),
        "off_300_pct": pct(tot["off300"], n),
        "jitter_sd": (math.sqrt(max(0.0, tot["sum_e2"] / n - (tot["sum_e"] / n) ** 2))
                      if n else float("nan")),
        "jitter_sd_within": math.sqrt(tot["ss_within"] / n) if n else float("nan"),
        "bias_correction_gain": (tot["gain"] / tot["gain_n"]) if tot["gain_n"] else float("nan"),
        "dense_rate": pct(tot["dense_ok"], tot["n_dense"]),
        "dense_rate_ci": wilson(tot["dense_ok"], tot["n_dense"]),
        "dense_n": tot["n_dense"],
        "sparse_rate": pct(tot["sparse_ok"], tot["n_sparse"]),
        "sparse_rate_ci": wilson(tot["sparse_ok"], tot["n_sparse"]),
        "sparse_n": tot["n_sparse"],
        "dense_sd_within": (math.sqrt(tot["dense_ss"] / tot["n_dense_sd"])
                            if tot["n_dense_sd"] else float("nan")),
        "alt_rate": pct(tot["alt_dense_ok"], tot["n_alt_dense"]),
        "alt_rate_ci": wilson(tot["alt_dense_ok"], tot["n_alt_dense"]),
        "alt_n": tot["n_alt_dense"],
        "alt_share_of_fast": pct(tot["n_alt_dense"], tot["n_dense"]),
        "alt_sd_within": (math.sqrt(tot["alt_dense_ss"] / tot["n_alt_dense_sd"])
                          if tot["n_alt_dense_sd"] else float("nan")),
        "dense_sd_n": tot["n_dense_sd"],
        "sparse_sd_within": (math.sqrt(tot["sparse_ss"] / tot["n_sparse_sd"])
                             if tot["n_sparse_sd"] else float("nan")),
        "sparse_sd_n": tot["n_sparse_sd"],
    }
    out["degradation"] = (out["dense_sd_within"] / out["sparse_sd_within"]
                          if out["sparse_sd_within"] else float("nan"))
    out["alt_degradation"] = (out["alt_sd_within"] / out["sparse_sd_within"]
                              if out["sparse_sd_within"] else float("nan"))
    return out


# Run-length buckets, in display order. The top bucket is open-ended. Each bucket
# also carries its median note spacing, because long runs tend to occur on slower
# maps and a bare 300-rate per bucket is then not like for like.
RUN_LENGTH_BUCKETS = ((2, 2, "2"), (3, 5, "3-5"), (6, 10, "6-10"),
                      (11, 20, "11-20"), (21, 40, "21-40"), (41, 80, "41-80"),
                      (81, None, "81+"))


def run_length_bucket(n: int) -> str:
    """Bucket name for a run of n notes."""
    for lo, hi, name in RUN_LENGTH_BUCKETS:
        if n >= lo and (hi is None or n <= hi):
            return name
    return RUN_LENGTH_BUCKETS[-1][2]


def play_run_sums(play):
    """Per-play counts and sums for everything measured inside stream runs.

    Every entry is additive, so a set of plays pools by adding its members up.
    That is what lets the report's pooling and the two-player comparison's
    bootstrap run off one definition instead of two: `pool_run_sums` finalises
    these sums into the reported quantities, and resampling plays just produces a
    different set of sums.
    """
    s = {"n_runs": 0, "n_int": 0, "n_dev15": 0, "n_dev50": 0, "n_nearsim": 0,
         "n_same": 0, "sum_d2": 0.0, "n_alt": 0, "sum_alt": 0.0,
         "sum_alt2": 0.0, "sum_com2": 0.0, "n_entry": 0, "entry_off15": 0,
         "entry_off30": 0, "entry_devs": [], "gridvals": [], "offsets": [],
         "by_len": {name: [0, 0] for _, _, name in RUN_LENGTH_BUCKETS},
         "by_len_spacing": {name: [] for _, _, name in RUN_LENGTH_BUCKETS}}
    p = play
    ids_all = [i for i in range(len(p.err)) if p.run_len[i] > 0]
    if not ids_all:
        return s
    for rid in sorted({int(p.run_id[i]) for i in ids_all}):
        idx = [i for i in ids_all if p.run_id[i] == rid]
        if len(idx) < 2:
            continue
        s["n_runs"] += 1
        bucket = run_length_bucket(len(idx))
        for i in idx:
            s["by_len"][bucket][0] += 1
            s["by_len"][bucket][1] += int(abs(p.err[i]) <= p.w300)
        d = np.diff(p.err[idx])
        g = p.gap[idx][1:]
        if len(g):
            spacing = float(np.median(g))
            s["gridvals"].append(spacing)
            s["by_len_spacing"][bucket].append(spacing)
        s["n_int"] += len(d)
        s["sum_d2"] += float((d * d).sum())
        dev = d / g
        s["n_dev15"] += int((np.abs(dev) <= 0.15).sum())
        s["n_dev50"] += int((np.abs(dev) > 0.50).sum())
        s["n_nearsim"] += int(((g + d) < NEAR_SIM_MS).sum())
        b = p.button[idx]
        s["n_same"] += int((b[1:] == b[:-1]).sum())
        # entry: the first interval of the run
        f, fg = p.err[idx[1]] - p.err[idx[0]], p.gap[idx[1]]
        if fg > 0:
            entry_dev = f / fg
            s["entry_devs"].append(entry_dev)
            s["n_entry"] += 1
            s["entry_off15"] += int(abs(entry_dev) > 0.15)
            s["entry_off30"] += int(abs(entry_dev) > 0.30)
        # alternating triples for the phase decomposition
        for j in range(len(idx) - 2):
            if b[j] == b[j + 1] or b[j + 1] == b[j + 2]:
                continue
            if abs(g[j] - g[j + 1]) > 0.15 * g[j]:
                continue
            a = (d[j] - d[j + 1]) / 2.0
            c = (d[j] + d[j + 1]) / 2.0
            s["sum_alt2"] += a * a
            s["sum_com2"] += c * c
            s["sum_alt"] += a
            s["n_alt"] += 1
        if len(idx) >= 4 and (b == 1).sum() >= 2 and (b == 2).sum() >= 2:
            s["offsets"].append(float(np.median(p.err[idx][b == 2])
                                      - np.median(p.err[idx][b == 1])))
    return s


RUN_SUM_KEYS = ("n_runs", "n_int", "n_dev15", "n_dev50", "n_nearsim", "n_same",
                "sum_d2", "n_alt", "sum_alt", "sum_alt2", "sum_com2", "n_entry",
                "entry_off15", "entry_off30")


def add_run_sums(a, b):
    """Add two per-play run-sum dicts."""
    out = {k: a[k] + b[k] for k in RUN_SUM_KEYS}
    out["entry_devs"] = a["entry_devs"] + b["entry_devs"]
    out["gridvals"] = a["gridvals"] + b["gridvals"]
    out["offsets"] = a["offsets"] + b["offsets"]
    out["by_len"] = {k: [a["by_len"][k][0] + b["by_len"][k][0],
                         a["by_len"][k][1] + b["by_len"][k][1]]
                     for k in a["by_len"]}
    out["by_len_spacing"] = {k: a["by_len_spacing"][k] + b["by_len_spacing"][k]
                             for k in a["by_len_spacing"]}
    return out


def pool_run_sums(sums):
    """Finalise pooled run sums into the reported quantities."""
    if not sums:
        return {}
    tot = sums[0]
    for s in sums[1:]:
        tot = add_run_sums(tot, s)
    out = {}
    nint = tot["n_int"]
    out["n_runs"] = tot["n_runs"]
    out["grid_ms"] = float(np.median(tot["gridvals"])) if tot["gridvals"] else float("nan")
    out["n_run_intervals"] = nint
    out["on_grid15"] = 100 * tot["n_dev15"] / nint if nint else float("nan")
    out["off_grid50"] = 100 * tot["n_dev50"] / nint if nint else float("nan")
    out["near_sim_pct"] = 100 * tot["n_nearsim"] / nint if nint else float("nan")
    out["alternation_pct"] = 100 * (nint - tot["n_same"]) / nint if nint else float("nan")
    out["interval_rms_ms"] = math.sqrt(tot["sum_d2"] / nint) if nint else float("nan")
    ed = tot["entry_devs"]
    out["entry_off15"] = (100 * tot["entry_off15"] / tot["n_entry"]) if tot["n_entry"] else float("nan")
    out["entry_off30"] = (100 * tot["entry_off30"] / tot["n_entry"]) if tot["n_entry"] else float("nan")
    out["entry_median_pct"] = 100 * float(np.median(ed)) if ed else float("nan")
    out["n_entry"] = len(ed)
    out["by_run_len"] = [{"bucket": k, "n": v[0],
                          "rate300": (100 * v[1] / v[0]) if v[0] else float("nan"),
                          "spacing_ms": (float(np.median(tot["by_len_spacing"][k]))
                                         if tot["by_len_spacing"].get(k) else float("nan"))}
                         for k, v in tot["by_len"].items()]
    if tot["n_alt"]:
        n = tot["n_alt"]
        ra = math.sqrt(tot["sum_alt2"] / n)
        rc = math.sqrt(tot["sum_com2"] / n)
        am = tot["sum_alt"] / n
        out["rms_alt"] = ra
        out["rms_com"] = rc
        out["phase_ms"] = math.sqrt(max(0.0, ra * ra - 3 * rc * rc))
        out["noise_ms"] = rc * math.sqrt(2)
        out["phase_over_noise"] = out["phase_ms"] / out["noise_ms"] if out["noise_ms"] else float("nan")
        out["hand_offset_systematic_ms"] = am
        out["phase_random_ms"] = math.sqrt(max(0.0, ra * ra - am * am))
        out["n_alt_pairs"] = n
    if tot["offsets"]:
        out["hand_offset_median_ms"] = float(np.median(tot["offsets"]))
        out["n_offset_runs"] = len(tot["offsets"])
    return out


def run_stats(plays, cfg=None):
    """Everything measured inside stream runs, pooled over plays."""
    return pool_run_sums([play_run_sums(p) for p in plays])


# --------------------------------------------------------------------- aim ---
MOVE_THRESHOLD = 0.5      # osu!px/ms; above this the cursor is still travelling
AIM_FREE_PX = 1.0         # a note this close to the previous one needs no cursor movement
JUMP_BANDS = ((0, 50, "0-50"), (50, 100, "50-100"), (100, 200, "100-200"),
              (200, None, "200+"))

AIM_SUM_KEYS = ("n_notes", "n_judged", "n_aim", "n_nopress", "n_press",
                "pr_judged", "pr_off", "pr_locked", "pr_window",
                "n_arrival", "n_late", "n_free")


def play_aim_sums(play):
    """Additive per-play aim sums.

    `aim_state` per note is 0 judged, 1 a press landed with the cursor off the
    circle, 2 no press at all, so notes that were never judged still carry a
    cause.
    """
    st = np.asarray(play.aim_state)
    pr = np.asarray(play.press_reasons)
    pb = np.asarray(play.press_bits)
    arr = np.asarray(play.aim_arrival, dtype=float)
    spd = np.asarray(play.aim_speed, dtype=float)
    jmp = np.asarray(play.aim_jump, dtype=float)
    err = np.asarray(play.aim_err, dtype=float)
    btn = np.asarray(play.aim_btn, dtype=int)
    free = np.asarray(play.aim_free, dtype=bool)
    ok = np.abs(err) <= play.w300
    fin = np.logical_and(np.isfinite(arr), np.logical_not(free))
    judged = np.nonzero(st == 0)[0]

    s = {"n_notes": int(len(st)), "n_judged": int(len(judged)),
         "n_aim": int((st == 1).sum()), "n_nopress": int((st == 2).sum()),
         "n_press": int(len(pr)), "pr_judged": int((pr == 0).sum()),
         "pr_off": int((pr == 1).sum()), "pr_locked": int((pr == 2).sum()),
         "pr_window": int((pr == 3).sum()),
         "n_arrival": int(fin.sum()), "n_late": int((arr[fin] > 0).sum()),
         "n_clamped": int(np.asarray(play.aim_arrival_clamped, dtype=bool).sum()),
         "n_free": int(free.sum()),
         "arrivals": arr[fin].tolist(),
         "moving": [0, 0, 0.0], "stationary": [0, 0, 0.0],
         "jump": {b[2]: [0, 0, 0.0] for b in JUMP_BANDS},
         "off_by_bit": {}, "by_bit": {},
         "n_speed": int(np.isfinite(spd).sum()),
         "speeds": spd[np.logical_and(np.isfinite(spd), np.logical_not(free))].tolist()}

    for bit in (1, 2, 4, 8):
        n_off = int(((pb == bit) & (pr == 1)).sum())
        if n_off:
            s["off_by_bit"][bit] = n_off
        sel = judged[btn[judged] == bit] if len(judged) else np.zeros(0, int)
        if len(sel):
            late = int((arr[sel][np.isfinite(arr[sel])] > 0).sum())
            s["by_bit"][bit] = [int(len(sel)), int(ok[sel].sum()), late]

    for i in judged:
        if free[i]:
            continue
        v = spd[i]
        if np.isfinite(v):
            bucket = s["moving"] if v > MOVE_THRESHOLD else s["stationary"]
            bucket[0] += 1
            bucket[1] += int(bool(ok[i]))
            bucket[2] += abs(float(err[i]))
        j = jmp[i]
        if np.isfinite(j):
            for lo, hi, name in JUMP_BANDS:
                if j >= lo and (hi is None or j < hi):
                    b = s["jump"][name]
                    b[0] += 1
                    b[1] += int(bool(ok[i]))
                    b[2] += abs(float(err[i]))
                    break
            else:
                b = s["jump"][JUMP_BANDS[-1][2]]
                b[0] += 1
                b[1] += int(bool(ok[i]))
                b[2] += abs(float(err[i]))
    return s


def pool_aim_sums(sums):
    """Finalise pooled aim sums into reported values."""
    if not sums:
        return {}
    tot = {k: 0 for k in AIM_SUM_KEYS}
    tot["n_clamped"] = 0
    AIM_SUM_KEYS_LOCAL = AIM_SUM_KEYS + ("n_clamped",)
    arrivals, speeds, n_speed = [], [], 0
    moving, stationary = [0, 0, 0.0], [0, 0, 0.0]
    jump = {b[2]: [0, 0, 0.0] for b in JUMP_BANDS}
    off_by_bit, by_bit = {}, {}
    for s in sums:
        for k in AIM_SUM_KEYS_LOCAL:
            tot[k] += s.get(k, 0)
        arrivals.extend(s["arrivals"])
        speeds.extend(s["speeds"])
        n_speed += s["n_speed"]
        for src, dst in ((s["moving"], moving), (s["stationary"], stationary)):
            for n in range(3):
                dst[n] += src[n]
        for k in jump:
            for n in range(3):
                jump[k][n] += s["jump"][k][n]
        for bit, v in s["off_by_bit"].items():
            off_by_bit[bit] = off_by_bit.get(bit, 0) + v
        for bit, v in s["by_bit"].items():
            cur = by_bit.setdefault(bit, [0, 0, 0])
            for n in range(3):
                cur[n] += v[n]

    def pct(num, den):
        return 100.0 * num / den if den else float("nan")

    misses = tot["n_aim"] + tot["n_nopress"]
    out = {
        "aim_notes": tot["n_notes"],
        "aim_judged": tot["n_judged"],
        "aim_miss_offcircle": tot["n_aim"],
        "aim_miss_nopress": tot["n_nopress"],
        "aim_miss_offcircle_pct": pct(tot["n_aim"], misses),
        "aim_miss_pct": pct(misses, tot["n_notes"]),
        "presses": tot["n_press"],
        "presses_offcircle": tot["pr_off"],
        "presses_offcircle_pct": pct(tot["pr_off"], tot["n_press"]),
        "presses_locked": tot["pr_locked"],
        "presses_locked_pct": pct(tot["pr_locked"], tot["n_press"]),
        "presses_outside_window": tot["pr_window"],
        "arrival_median_ms": float(np.median(arrivals)) if arrivals else float("nan"),
        "arrived_late_pct": pct(tot["n_late"], tot["n_arrival"]),
        "n_arrival": tot["n_arrival"],
        "arrival_unmeasurable": tot["n_clamped"],
        "aim_free_notes": tot["n_free"],
        "aim_free_pct": pct(tot["n_free"], tot["n_judged"]),
        "speed_median": float(np.median(speeds)) if speeds else float("nan"),
        "moving_n": moving[0], "moving_rate300": pct(moving[1], moving[0]),
        "moving_abs_err": (moving[2] / moving[0]) if moving[0] else float("nan"),
        "stationary_n": stationary[0], "stationary_rate300": pct(stationary[1], stationary[0]),
        "stationary_abs_err": (stationary[2] / stationary[0]) if stationary[0] else float("nan"),
        "aim_by_input": [{"bit": bit, "name": BUTTON_LABEL.get(bit, f"bit {bit}"),
                          "judged": v[0], "rate300": pct(v[1], v[0]),
                          "late_pct": pct(v[2], v[0]),
                          "offcircle_presses": off_by_bit.get(bit, 0)}
                         for bit, v in sorted(by_bit.items())],
        "aim_by_jump": [{"band": name, "n": jump[name][0],
                         "rate300": pct(jump[name][1], jump[name][0]),
                         "abs_err": (jump[name][2] / jump[name][0]) if jump[name][0] else float("nan")}
                        for _, _, name in JUMP_BANDS],
    }
    return out


# ---------------------------------------------------------------- patterns ---
# Separation between consecutive notes, in circle diameters. One diameter is the
# point where the two circles stop overlapping, which is what separates a stacked
# stream from a spaced one.
SEP_BANDS = ((0.0, 0.5, "under 0.5"), (0.5, 1.0, "0.5-1.0"),
             (1.0, 1.5, "1.0-1.5"), (1.5, 2.0, "1.5-2.0"), (2.0, None, "2.0+"))
PATTERN_CLASSES = ("stacked stream", "spaced stream",
                   "single-tapped, stacked", "single-tapped, spaced",
                   "isolated fast notes", "slow notes")


def play_pattern_sums(play):
    """Additive per-play sums by pattern class and by separation.

    A note is in a stream when its snap divisor is 1/4 or denser. Inside a stream,
    separation is the distance from the previous note divided by the circle
    diameter, so 1.0 is where the circles stop overlapping.
    """
    n = len(np.asarray(play.aim_err))
    empty = {"classes": {k: [0, 0, 0, 0] for k in PATTERN_CLASSES},
             "alt": {k: [0, 0] for k in PATTERN_CLASSES},
             "bands": {b[2]: [0, 0, 0.0] for b in SEP_BANDS}}
    if not n:
        return empty
    err = np.asarray(play.aim_err, dtype=float)
    ok = np.abs(err) <= play.w300
    jump = np.asarray(play.aim_jump, dtype=float)
    div = np.asarray(play.aim_divisor, dtype=int)
    r = float(play.radius_px)
    sep = jump / (2.0 * r) if r and r == r else np.full(n, np.nan)
    stream = div >= STREAM_DIVISOR
    finite = np.isfinite(sep)
    s = {"classes": {k: [0, 0, 0, 0] for k in PATTERN_CLASSES},
         "alt": {k: [0, 0] for k in PATTERN_CLASSES},
         "bands": {b[2]: [0, 0, 0.0] for b in SEP_BANDS}}
    # a note only counts as part of a stream when a neighbouring note is also 1/4
    # or denser, so an isolated fast note is not mistaken for stream play
    prev = np.concatenate(([False], stream[:-1]))
    nxt = np.concatenate((stream[1:], [False]))
    in_run = np.logical_and(stream, np.logical_or(prev, nxt))
    bt = np.asarray(play.aim_btn, dtype=int)
    # a note is alternated when it was struck with a different input than the note
    # before it; a stream is by definition alternated, so a single-tapped run is
    # reported separately rather than counted as one
    alt_here = np.zeros(len(div), dtype=bool)
    if len(div) > 1:
        alt_here[1:] = bt[1:] != bt[:-1]
    stacked = np.logical_and(in_run, np.logical_and(finite, sep < 1.0))
    spaced = np.logical_and(in_run, np.logical_and(finite, sep >= 1.0))
    for name, m in (("stacked stream", np.logical_and(stacked, alt_here)),
                    ("spaced stream", np.logical_and(spaced, alt_here)),
                    ("single-tapped, stacked", np.logical_and(stacked, np.logical_not(alt_here))),
                    ("single-tapped, spaced", np.logical_and(spaced, np.logical_not(alt_here))),
                    ("isolated fast notes", np.logical_and(stream, np.logical_not(in_run))),
                    ("slow notes", np.logical_not(stream))):
        pen_mask = np.logical_and(m, bt == 1)
        s["classes"][name] = [int(m.sum()), int(ok[m].sum()),
                              int(pen_mask.sum()), int(ok[pen_mask].sum())]
        both = np.logical_and(m[:-1], m[1:])
        s["alt"][name][0] = int(both.sum())
        s["alt"][name][1] = int((bt[:-1][both] != bt[1:][both]).sum())
    for lo, hi, name in SEP_BANDS:
        m = np.logical_and(np.logical_and(stream, finite), sep >= lo)
        m = np.logical_and(m, sep < (hi if hi is not None else 1e9))
        b = s["bands"][name]
        b[0] = int(m.sum())
        b[1] = int(ok[m].sum())
        e = np.abs(err[m])
        b[2] = float(e[np.isfinite(e)].sum())
    return s


def pool_pattern_sums(sums):
    """Finalise pooled pattern sums into reported values."""
    if not sums:
        return {}
    cls = {k: [0, 0, 0, 0] for k in PATTERN_CLASSES}
    alt = {k: [0, 0] for k in PATTERN_CLASSES}
    bands = {b[2]: [0, 0, 0.0] for b in SEP_BANDS}
    for s in sums:
        for k in cls:
            v = s["classes"].get(k, [0, 0, 0, 0])
            for i in range(4):
                cls[k][i] += v[i]
            a = s["alt"].get(k, [0, 0])
            alt[k][0] += a[0]
            alt[k][1] += a[1]
        for k in bands:
            v = s["bands"].get(k, [0, 0, 0.0])
            for i in range(3):
                bands[k][i] += v[i]

    def pct(a, b):
        return 100.0 * a / b if b else float("nan")

    out = {"pattern_by_class": [{"pattern": k, "n": cls[k][0],
                                 "rate300": pct(cls[k][1], cls[k][0]),
                                 "pen_pct": pct(cls[k][2], cls[k][0]),
                                 "rate300_pen": pct(cls[k][3], cls[k][2]),
                                 "pen_n": cls[k][2],
                                 "alternation_pct": pct(alt[k][1], alt[k][0])}
                                for k in PATTERN_CLASSES],
           "pattern_by_sep": [{"band": name, "n": bands[name][0],
                               "rate300": pct(bands[name][1], bands[name][0]),
                               "abs_err": (bands[name][2] / bands[name][0]) if bands[name][0] else float("nan")}
                              for _, _, name in SEP_BANDS]}
    # where accuracy first falls away from the tightest separation played
    usable = [r for r in out["pattern_by_sep"]
              if r["n"] >= CEIL_MIN_TAPS and r["rate300"] == r["rate300"]]
    knee = None
    base = usable[0] if usable else None
    if base:
        for row in usable[1:]:
            if row["rate300"] <= base["rate300"] - 5.0:
                knee = row
                break
    out["pattern_sep_baseline"] = base["rate300"] if base else float("nan")
    out["pattern_sep_baseline_band"] = base["band"] if base else None
    out["pattern_sep_knee"] = knee["band"] if knee else None
    out["pattern_sep_knee_rate"] = knee["rate300"] if knee else float("nan")
    out["pattern_sep_knee_n"] = knee["n"] if knee else 0
    out["pattern_sep_widest"] = usable[-1]["band"] if usable else None
    out["pattern_sep_widest_rate"] = usable[-1]["rate300"] if usable else float("nan")
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
    s["aim"] = {
        "notes": agg.get("aim_notes"),
        "notes_accepted": agg.get("aim_judged"),
        "lost_to_aim": agg.get("aim_miss_offcircle"),
        "lost_no_press": agg.get("aim_miss_nopress"),
        "lost_to_aim_share": agg.get("aim_miss_offcircle_pct"),
        "presses_thrown_away_for_position": agg.get("presses_offcircle"),
        "presses_thrown_away_share": agg.get("presses_offcircle_pct"),
        "presses_refused_by_note_lock": agg.get("presses_locked"),
        "arrival_median_ms": agg.get("arrival_median_ms"),
        "arrived_after_note_share": agg.get("arrived_late_pct"),
        "no_movement_required": agg.get("aim_free_notes"),
        "no_movement_required_share": agg.get("aim_free_pct"),
        "rate300_still_travelling": agg.get("moving_rate300"),
        "rate300_settled": agg.get("stationary_rate300"),
    }
    s["tool"] = {"name": "osu-replay-report", "version": "1.0",
                 "ceil_300": cfg.get("ceil_300", DEFAULT_CEIL_300),
                 "ceil_min_taps": cfg.get("ceil_min_taps", CEIL_MIN_TAPS)}
    return s
