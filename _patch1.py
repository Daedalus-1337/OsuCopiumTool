from pathlib import Path
p = Path('replaymetrics.py'); s = p.read_text(encoding='utf-8')

# --- 1. dataclass: the BPM actually played, not just the extremes
s = s.replace('''    map_bpm_min: float = float("nan")
    map_bpm_max: float = float("nan")''',
'''    map_bpm_min: float = float("nan")
    map_bpm_max: float = float("nan")
    map_bpm_used: float = float("nan")   # median BPM at the notes actually played''')

# --- 2. compute it
s = s.replace('''    bpm_lo, bpm_hi = bm.bpm_range()
    m = hit''',
'''    bpm_lo, bpm_hi = bm.bpm_range()
    m = hit
    played_bpm = []
    for i in range(nb):
        if not hit[i]:
            continue
        bl = beat_length_at(bm, nt_map[i])
        if bl and bl > 0:
            played_bpm.append(60000.0 / bl)''')

s = s.replace('''        map_bpm_min=bpm_lo or float("nan"), map_bpm_max=bpm_hi or float("nan"),''',
'''        map_bpm_min=bpm_lo or float("nan"), map_bpm_max=bpm_hi or float("nan"),
        map_bpm_used=float(np.median(played_bpm)) if played_bpm else float("nan"),''')

# --- 3. replace the speed section
old_start = '    # ---- accuracy by effective BPM (the speed curve)'
old_end = '    # ---- playstyle fingerprint'
i0, i1 = s.index(old_start), s.index(old_end)
new = '''    # ---- accuracy by effective BPM
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

'''
s = s[:i0] + new + s[i1:]

# --- 4. summary keys
s = s.replace('''            "sparse_sd_within", "on_grid15", "off_grid50", "near_sim_pct",''',
'''            "sparse_sd_within", "dense_rate", "sparse_rate", "dense_n",
            "sparse_n", "on_grid15", "off_grid50", "near_sim_pct",''')

s = s.replace('''    sk = agg.get("speed_knee")
    s["speed_knee_bpm"] = ((sk["lo"] + sk["hi"]) / 2) if sk else None''',
'''    sk = agg.get("speed_knee")
    s["speed_knee_bpm"] = ((sk["lo"] + sk["hi"]) / 2) if sk else None
    sh = agg.get("speed_held_to_baseline")
    s["speed_held_to_baseline_bpm"] = ((sh["lo"] + sh["hi"]) / 2) if sh else None''')

p.write_text(s, encoding='utf-8')
print('patched ok')
