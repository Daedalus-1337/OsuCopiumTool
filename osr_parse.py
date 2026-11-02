"""Minimal, dependency-free parser for osu! .osr replay files.

Format source: osu! wiki "osu! file formats / .osr".
All strings use the osu binary string encoding: a presence byte (0x00 = empty,
0x0b = present), then ULEB128 length, then UTF-8 bytes.
Replay data is a raw LZMA1 stream (13-byte FORMAT_ALONE header).
"""
from __future__ import annotations

import datetime as _dt
import lzma
import struct
from dataclasses import dataclass, field


class _R:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n: int) -> bytes:
        b = self.d[self.i:self.i + n]
        if len(b) != n:
            raise EOFError(f"wanted {n} bytes at {self.i}, got {len(b)}")
        self.i += n
        return b

    def byte(self) -> int:
        return self.take(1)[0]

    def ushort(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def int(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def uint(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def long(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def uleb128(self) -> int:
        result = 0
        shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7

    def string(self) -> str:
        present = self.byte()
        if present == 0x00:
            return ""
        if present != 0x0B:
            raise ValueError(f"bad string presence byte 0x{present:02x} at {self.i - 1}")
        return self.take(self.uleb128()).decode("utf-8", "replace")


MOD_BITS = {
    1: "NF", 2: "EZ", 4: "TD", 8: "HD", 16: "HR", 32: "SD", 64: "DT",
    128: "RX", 256: "HT", 512: "NC", 1024: "FL", 2048: "AT", 4096: "SO",
    8192: "AP", 16384: "PF", 32768: "4K", 65536: "5K", 131072: "6K",
    262144: "7K", 524288: "8K", 1048576: "FI", 2097152: "RD",
    4194304: "CN", 8388608: "TG", 16777216: "9K", 33554432: "10K",
    67108864: "1K", 134217728: "3K", 268435456: "2K", 536870912: "V2",
    1073741824: "MR",
}

# Input bit flags carried in the 4th field of every replay frame.
KEY_BITS = {
    1: "M1",   # left mouse button  (usually the tablet pen tip)
    2: "M2",   # right mouse button
    4: "K1",   # keyboard tap key 1
    8: "K2",   # keyboard tap key 2
    16: "SMOKE",
}


def decode_mods(mods: int) -> str:
    if mods == 0:
        return "NM"
    out = [name for bit, name in sorted(MOD_BITS.items()) if mods & bit]
    return "".join(out) if out else f"0x{mods:x}"


@dataclass
class Frame:
    """One cursor/input sample. `t` is ms from the start of the play."""
    t: int
    x: float
    y: float
    keys: int

    @property
    def pressed(self) -> set:
        return {name for bit, name in KEY_BITS.items() if self.keys & bit}


@dataclass
class Replay:
    mode: int
    game_version: int
    beatmap_hash: str
    username: str
    replay_hash: str
    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int
    score: int
    max_combo: int
    perfect: bool
    mods: int
    life_bar: str
    timestamp: int          # .NET ticks
    replay_length: int
    replay_data: str
    online_id: int | None = None
    frames: list = field(default_factory=list)

    # ---- derived ------------------------------------------------------
    @property
    def mods_str(self) -> str:
        return decode_mods(self.mods)

    @property
    def played_at(self):
        if self.timestamp <= 0:
            return None
        unix = self.timestamp / 10_000_000 - 62135596800
        return _dt.datetime.fromtimestamp(unix, _dt.timezone.utc)

    @property
    def total_objects(self) -> int:
        return self.count_300 + self.count_100 + self.count_50 + self.count_miss

    @property
    def acc(self) -> float:
        n = self.total_objects
        if not n:
            return 0.0
        return (300 * self.count_300 + 100 * self.count_100 + 50 * self.count_50) / (300 * n) * 100


def _decompress(blob: bytes) -> str:
    """Raw LZMA1. Some replays carry a bogus uncompressed-size field in the
    13-byte header; retry with the size marked unknown (0xFFFFFFFFFFFFFFFF)."""
    attempts = [blob]
    if len(blob) > 13:
        attempts.append(blob[:5] + b"\xff" * 8 + blob[13:])
    last = None
    for buf in attempts:
        try:
            d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            out = d.decompress(buf)
            if out:
                return out.decode("ascii", "replace")
        except lzma.LZMAError as e:      # pragma: no cover - fallback path
            last = e
    raise ValueError(f"could not decompress replay data: {last}")


def parse_frames(replay_data: str) -> list:
    """Replay data is `delta_ms|x|y|keys` events joined by commas."""
    frames = []
    t = 0
    for chunk in replay_data.split(","):
        if not chunk:
            continue
        parts = chunk.split("|")
        if len(parts) < 4:
            continue
        try:
            t += int(parts[0])
            x = float(parts[1])
            y = float(parts[2])
            k = int(parts[3])
        except ValueError:
            continue
        frames.append(Frame(t, x, y, k))
    return frames


def parse(data: bytes) -> Replay:
    r = _R(data)
    mode = r.byte()
    game_version = r.int()
    beatmap_hash = r.string()
    username = r.string()
    replay_hash = r.string()
    count_300 = r.ushort()
    count_100 = r.ushort()
    count_50 = r.ushort()
    count_geki = r.ushort()
    count_katu = r.ushort()
    count_miss = r.ushort()
    score = r.int()
    max_combo = r.ushort()
    perfect = bool(r.byte())
    mods = r.uint()
    life_bar = r.string()
    timestamp = r.long()
    replay_length = r.int()
    blob = r.take(replay_length)
    online_id = None
    if len(r.d) - r.i >= 8:
        online_id = r.long()

    replay_data = _decompress(blob)
    rep = Replay(mode, game_version, beatmap_hash, username, replay_hash,
                 count_300, count_100, count_50, count_geki, count_katu,
                 count_miss, score, max_combo, perfect, mods, life_bar,
                 timestamp, replay_length, replay_data, online_id)
    rep.frames = parse_frames(replay_data)
    return rep


def parse_header(data: bytes) -> Replay:
    """Everything except the input data - no LZMA, no frames. For indexing
    thousands of replays where only the score summary matters."""
    r = _R(data)
    mode = r.byte()
    game_version = r.int()
    beatmap_hash = r.string()
    username = r.string()
    replay_hash = r.string()
    count_300 = r.ushort()
    count_100 = r.ushort()
    count_50 = r.ushort()
    count_geki = r.ushort()
    count_katu = r.ushort()
    count_miss = r.ushort()
    score = r.int()
    max_combo = r.ushort()
    perfect = bool(r.byte())
    mods = r.uint()
    life_bar = r.string()
    timestamp = r.long()
    replay_length = r.int()
    online_id = None
    if len(r.d) - r.i >= 8:
        online_id = r.long()
    return Replay(mode, game_version, beatmap_hash, username, replay_hash,
                  count_300, count_100, count_50, count_geki, count_katu,
                  count_miss, score, max_combo, perfect, mods, life_bar,
                  timestamp, replay_length, "", online_id)


def load_header(path, probe=8192) -> Replay:
    """Read only the head of the file; fall back to the whole file if the
    header somehow runs past the probe."""
    with open(path, "rb") as f:
        head = f.read(probe)
        try:
            return parse_header(head)
        except (EOFError, ValueError):
            f.seek(0)
            return parse_header(f.read())


def load(path) -> Replay:
    with open(path, "rb") as f:
        return parse(f.read())


if __name__ == "__main__":
    import sys
    rp = load(sys.argv[1])
    print(f"player      : {rp.username}")
    print(f"mode        : {rp.mode}   game version: {rp.game_version}")
    print(f"beatmap hash: {rp.beatmap_hash}")
    print(f"mods        : {rp.mods_str} ({rp.mods})")
    print(f"played at   : {rp.played_at}")
    print(f"score       : {rp.score:,}   max combo: {rp.max_combo}   perfect: {rp.perfect}")
    print(f"300/100/50/x: {rp.count_300}/{rp.count_100}/{rp.count_50}/{rp.count_miss}"
          f"   acc {rp.acc:.2f}%")
    print(f"frames      : {len(rp.frames)}")
