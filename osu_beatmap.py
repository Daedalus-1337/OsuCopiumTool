"""Parser for osu! .osu beatmap files (osu!standard focus, but header parse is
format-generic). Only what the analyser needs: difficulty settings, timing
points and hit objects with their start times."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# HitObject type bits
TYPE_CIRCLE = 1
TYPE_SLIDER = 2
TYPE_SPINNER = 8
TYPE_HOLD = 128


@dataclass
class TimingPoint:
    time: float
    beat_length: float      # ms per beat if uninherited, else negative SV multiplier
    meter: int
    uninherited: bool

    @property
    def sv(self) -> float:
        """Slider velocity multiplier in effect from this point."""
        if self.uninherited:
            return 1.0
        return max(0.1, min(10.0, -100.0 / self.beat_length))


@dataclass
class HitObject:
    x: float
    y: float
    time: float
    kind: int
    hitsound: int
    end_time: float = 0.0
    slider_points: int = 0
    slides: int = 0
    length: float = 0.0

    @property
    def is_circle(self) -> bool:
        return bool(self.kind & TYPE_CIRCLE)

    @property
    def is_slider(self) -> bool:
        return bool(self.kind & TYPE_SLIDER)

    @property
    def is_spinner(self) -> bool:
        return bool(self.kind & TYPE_SPINNER)

    @property
    def label(self) -> str:
        return ("circle" if self.is_circle else
                "slider" if self.is_slider else
                "spinner" if self.is_spinner else f"type{self.kind}")


@dataclass
class Beatmap:
    path: str
    format_version: int = 14
    mode: int = 0
    title: str = ""
    artist: str = ""
    creator: str = ""
    version: str = ""            # difficulty name
    beatmap_id: int = 0
    beatmapset_id: int = 0
    ar: float = 5.0
    od: float = 5.0
    cs: float = 4.0
    hp: float = 5.0
    slider_multiplier: float = 1.4
    slider_tick_rate: float = 1.0
    timing_points: list = field(default_factory=list)
    objects: list = field(default_factory=list)
    raw: str = ""

    # ---- derived ------------------------------------------------------
    def base_beat_length(self, at_time: float = 0.0) -> float:
        bl = 500.0
        for tp in self.timing_points:
            if tp.uninherited and tp.time <= at_time:
                bl = tp.beat_length
        if not any(tp.uninherited for tp in self.timing_points):
            for tp in self.timing_points:
                if tp.uninherited:
                    bl = tp.beat_length
                    break
        return bl if bl > 0 else 500.0

    def bpm_range(self):
        bpms = [60000.0 / tp.beat_length for tp in self.timing_points
                if tp.uninherited and tp.beat_length > 0]
        if not bpms:
            return (None, None)
        return (min(bpms), max(bpms))

    def counts(self) -> dict:
        c = {"circle": 0, "slider": 0, "spinner": 0, "other": 0}
        for o in self.objects:
            if o.is_circle:
                c["circle"] += 1
            elif o.is_slider:
                c["slider"] += 1
            elif o.is_spinner:
                c["spinner"] += 1
            else:
                c["other"] += 1
        return c


def _num(d: dict, key: str, default: float) -> float:
    try:
        return float(d.get(key, default))
    except (TypeError, ValueError):
        return default


def parse(path) -> Beatmap:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    bm = Beatmap(path=str(path), raw=text)
    section = None
    kv: dict = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue

        if section == "General":
            if ":" in line:
                k, v = line.split(":", 1)
                kv[k.strip()] = v.strip()
        elif section == "Metadata":
            if ":" in line:
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if k == "Title":
                    bm.title = v
                elif k == "Artist":
                    bm.artist = v
                elif k == "Creator":
                    bm.creator = v
                elif k == "Version":
                    bm.version = v
                elif k == "BeatmapID":
                    bm.beatmap_id = int(v) if v.lstrip("-").isdigit() else 0
                elif k == "BeatmapSetID":
                    bm.beatmapset_id = int(v) if v.lstrip("-").isdigit() else 0
        elif section == "Difficulty":
            if ":" in line:
                k, v = line.split(":", 1)
                kv[k.strip()] = v.strip()
        elif section == "TimingPoints":
            parts = line.split(",")
            if len(parts) >= 7:
                try:
                    tp = TimingPoint(float(parts[0]), float(parts[1]),
                                     int(float(parts[2])),
                                     parts[6].strip() == "1")
                except ValueError:
                    continue
                bm.timing_points.append(tp)
        elif section == "HitObjects":
            parts = line.split(",")
            if len(parts) < 4:
                continue
            try:
                o = HitObject(float(parts[0]), float(parts[1]), float(parts[2]),
                              int(parts[3]), int(parts[4]) if len(parts) > 4 else 0)
            except ValueError:
                continue
            rest = parts[5:]
            if o.is_slider and rest:
                curve = rest[0] if rest else ""
                o.slider_points = max(0, curve.count("|"))
                try:
                    o.slides = int(rest[1]) if len(rest) > 1 else 1
                    o.length = float(rest[2]) if len(rest) > 2 else 0.0
                except ValueError:
                    pass
                bl = bm.base_beat_length(o.time)
                sv = 1.0
                for tp in bm.timing_points:
                    if tp.time <= o.time:
                        sv = tp.sv if not tp.uninherited else 1.0
                px_per_beat = bm.slider_multiplier * 100.0 * sv
                dur = (o.length / px_per_beat) * bl if px_per_beat else 0.0
                o.end_time = o.time + dur * max(1, o.slides)
            elif o.is_spinner and rest:
                try:
                    o.end_time = float(rest[0])
                except ValueError:
                    pass
            else:
                o.end_time = o.time
            bm.objects.append(o)

    bm.objects.sort(key=lambda o: o.time)
    if "Mode" in kv:
        try:
            bm.mode = int(float(kv["Mode"]))
        except ValueError:
            pass
    bm.od = _num(kv, "OverallDifficulty", 5.0)
    bm.ar = _num(kv, "ApproachRate", bm.od)
    bm.cs = _num(kv, "CircleSize", 4.0)
    bm.hp = _num(kv, "HPDamage", 5.0)
    bm.slider_multiplier = _num(kv, "SliderMultiplier", 1.4)
    bm.slider_tick_rate = _num(kv, "SliderTickRate", 1.0)
    if "BeatmapVersion" in kv:
        pass
    # format version is the first non-empty line, e.g. "osu file format v14"
    for line in text.splitlines():
        line = line.strip()
        if line:
            if "osu file format v" in line:
                try:
                    bm.format_version = int(line.rsplit("v", 1)[1])
                except ValueError:
                    pass
            break
    return bm


if __name__ == "__main__":
    import sys
    b = parse(sys.argv[1])
    lo, hi = b.bpm_range()
    print(f"{b.artist} - {b.title} [{b.version}] by {b.creator}")
    print(f"format v{b.format_version}  mode {b.mode}  AR{b.ar} OD{b.od} CS{b.cs} HP{b.hp}")
    print(f"slider mult {b.slider_multiplier}  tick rate {b.slider_tick_rate}")
    print(f"BPM {lo:.2f}-{hi:.2f}  timing points {len(b.timing_points)}")
    print(f"objects: {b.counts()}  total {len(b.objects)}")
    if b.objects:
        print(f"time span {b.objects[0].time:.0f} .. {b.objects[-1].end_time or b.objects[-1].time:.0f} ms")
