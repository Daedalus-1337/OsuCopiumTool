"""Rendering for osu! replay reports: markdown, self-contained HTML, charts.

The report is written for a person who wants to know what their replays show,
not for a paper: the headline block answers the questions a player actually has,
the tables carry the detail, and every claim that rests on a thin sample says so
where it is made.
"""
from __future__ import annotations

import base64
import html as _html
import io
import math
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402

INK = "#1b1b1b"
MUTED = "#6b6b6b"
ACCENT = "#c1440e"
SECOND = "#2b6cb0"
GRID = "#d8d8d8"
FIGSIZE = (7.2, 2.9)


def _num(v, fmt="{:.1f}", dash="—"):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    if not math.isfinite(f):
        return dash
    return fmt.format(f)


def _bpm_band(r):
    if not r:
        return "—"
    return f"{r['lo']:.0f}–{r['hi']:.0f} BPM"


def _nps(bpm):
    return _num(bpm / 15.0 if bpm == bpm else float("nan"), "{:.1f}")


# ------------------------------------------------------------------- charts ---
def _save(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def chart_speed(agg):
    alt = [r for r in agg.get("by_bpm_alt", []) if r["n"] >= 10]
    rows = alt if len(alt) >= 2 else [r for r in agg.get("by_bpm_stream", []) if r["n"] >= 10]
    if len(rows) < 2:
        return None
    fig, ax = plt.subplots(figsize=FIGSIZE)
    x = [r["lo"] + 10 for r in rows]
    y = [r["rate300"] for r in rows]
    n = [r["n"] for r in rows]
    err = [[r["rate300"] - r["ci"][0] for r in rows],
           [r["ci"][1] - r["rate300"] for r in rows]]
    ax.errorbar(x, y, yerr=err, marker="o", ms=5, lw=1.6, color=ACCENT,
                ecolor="#e3b7a3", capsize=2.5, zorder=3)
    base = agg.get("sparse_rate")
    if base == base:
        ax.axhline(base, color=MUTED, ls="--", lw=1.1, zorder=1)
        ax.annotate("accuracy on 1/1–1/2 notes", (x[0], base), xytext=(2, 4),
                    textcoords="offset points", fontsize=7.5, color=MUTED)
        ax.axhline(base - 5, color=GRID, lw=1, zorder=1)
    for xi, yi, ni in zip(x, y, n):
        ax.annotate(f"{ni}", (xi, yi), xytext=(0, -13), textcoords="offset points",
                    fontsize=6.5, color=MUTED, ha="center")
    ax.set_xlabel("effective BPM (alternated 1/4 notes)" if len(alt) >= 2
                  else "effective BPM (1/4 notes or denser)", fontsize=8.5)
    ax.set_ylabel("300-rate %", fontsize=8.5)
    ax.set_ylim(max(0, min(y) - 8), 102)
    ax.tick_params(labelsize=7.5)
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig)


def chart_errors(plays, agg):
    err = np.concatenate([p.err for p in plays])
    if not len(err):
        return None
    w = agg.get("w300_median", 28.0)
    fig, ax = plt.subplots(figsize=FIGSIZE)
    lim = max(60.0, float(np.percentile(np.abs(err), 99.5)) * 1.05)
    bins = np.linspace(-lim, lim, 61)
    ax.axvspan(-w, w, color="#fdece4", zorder=0)
    ax.hist(np.clip(err, -lim, lim), bins=bins, color=SECOND, alpha=0.85, zorder=2)
    ax.axvline(0, color=INK, lw=1.0, zorder=3)
    ax.axvline(float(np.median(err)), color=ACCENT, lw=1.4, ls="--", zorder=3)
    ax.annotate(f"median {np.median(err):+.1f} ms", (float(np.median(err)), ax.get_ylim()[1]),
                xytext=(3, -9), textcoords="offset points", fontsize=7.5, color=ACCENT)
    ax.annotate(f"300 window ±{w:.0f} ms", (w, ax.get_ylim()[1]), xytext=(-4, -20),
                textcoords="offset points", fontsize=7.5, color=MUTED, ha="right")
    ax.set_xlabel("tap error, ms (negative = early)", fontsize=8.5)
    ax.set_ylabel("taps", fontsize=8.5)
    ax.tick_params(labelsize=7.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig)


def chart_runlen(agg):
    rows = [r for r in agg.get("by_run_len", []) if r["n"] >= 15]
    if len(rows) < 2:
        return None
    fig, ax = plt.subplots(figsize=FIGSIZE)
    xs = [r["bucket"] for r in rows]
    ys = [r["rate300"] for r in rows]
    ax.bar(range(len(xs)), ys, color=SECOND, alpha=0.85, width=0.7, zorder=2)
    for i, (r, y) in enumerate(zip(rows, ys)):
        ax.annotate(f"{r['rate300']:.0f}%\n{nfmt(r['n'])}", (i, y), xytext=(0, 3),
                    textcoords="offset points", fontsize=6.5, ha="center", color=INK)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, fontsize=8)
    ax.set_xlabel("notes in the unbroken run", fontsize=8.5)
    ax.set_ylim(0, 108)
    ax.set_ylabel("300-rate %", fontsize=8.5)
    ax.tick_params(labelsize=7.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig)


def chart_inputs(agg):
    rows = [r for r in agg.get("by_divisor", []) if r["n"] >= 30]
    if len(rows) < 2:
        return None
    fig, ax = plt.subplots(figsize=FIGSIZE)
    xs = [r["label"] for r in rows]
    ys = [r["second_input_share"] for r in rows]
    ax.bar(range(len(xs)), ys, color=ACCENT, alpha=0.85, width=0.6, zorder=2)
    ax.axhline(50, color=MUTED, ls="--", lw=1.0, zorder=3)
    ax.set_ylim(0, 100)
    for i, y in enumerate(ys):
        ax.annotate(f"{y:.0f}%", (i, y), xytext=(0, 3), textcoords="offset points",
                    fontsize=7.5, ha="center", color=INK)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, fontsize=8.5)
    ax.set_xlabel("note spacing, as a fraction of a beat", fontsize=8.5)
    ax.set_ylabel("share on 2nd input, %", fontsize=8.5)
    ax.tick_params(labelsize=7.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return _save(fig)


def nfmt(n):
    return f"{n:,}"


# -------------------------------------------------------------------- tables ---
def table(headers, rows, align=None):
    if not rows:
        return ""
    align = align or ["left"] + ["right"] * (len(headers) - 1)
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(":--" if a == "left" else "--:" for a in align) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------- headline ---
def headline_rows(agg, cfg=None):
    cfg = cfg or {}
    fp = agg.get("fingerprint") or {}
    stars = (_num(agg.get("star_min"), "{:.2f}"), _num(agg.get("star_max"), "{:.2f}"))
    bpm = (agg.get("played_bpm_min"), agg.get("played_bpm_max"))
    bpm_txt = ("—" if bpm[0] != bpm[0] else
               (f"{bpm[0]:.0f}" if abs(bpm[0] - bpm[1]) < 1 else f"{bpm[0]:.0f}–{bpm[1]:.0f}"))
    ceiling = agg.get("speed_ceiling")
    fastest = agg.get("speed_fastest")
    knee = agg.get("speed_knee")
    speed_txt = "no 1/4-or-denser notes with enough taps"
    if fastest:
        basis = ("alternated " if agg.get("speed_basis") == "alternated stream taps" else "")
        speed_txt = (f"reaches {_bpm_band(fastest)} at "
                     f"{_num(fastest['rate300'], '{:.1f}')}% "
                     f"({nfmt(fastest['n'])} taps)")
        if basis:
            speed_txt = basis + speed_txt
        if ceiling:
            speed_txt += (f"; holds the {cfg.get('ceil_300', 90):.0f}% bar up to "
                          f"{_bpm_band(ceiling)}")
    rows = [
        ("Playstyle", f"{fp.get('verdict', '—')}"),
        ("Maps played", f"{stars[0]}★ – {stars[1]}★ &nbsp;·&nbsp; {bpm_txt} BPM"),
        ("Notes judged", f"{nfmt(agg.get('n_taps', 0))} taps over {nfmt(agg.get('n_plays', 0))} replays"),
        ("Accuracy", f"{_num(agg.get('acc_300_rate'), '{:.1f}')}% of taps inside the 300 window "
                     f"&nbsp;·&nbsp; reported accuracy {_num(agg.get('acc_reported'), '{:.1f}')}% "
                     f"<span class='note'>— the game's own weighted figure, not the same quantity</span>"),
        ("Timing", f"{_num(agg.get('bias'), '{:+.1f}')} ms mean, "
                   f"{_num(agg.get('jitter_sd_within'), '{:.1f}')} ms scatter"),
        ("Stream speed", speed_txt),
        ("Accuracy drops at", (f"{_bpm_band(knee)}" if knee else
                               "no drop found — 1/4 accuracy holds the level of the slower notes")),
    ]
    if agg.get("n_alt_pairs"):
        rows.append(("Hand-to-hand", (
            f"{_num(agg.get('phase_ms'), '{:.1f}')} ms separation between the two inputs, "
            f"against {_num(agg.get('noise_ms'), '{:.1f}')} ms of jitter within each "
            f"(ratio {_num(agg.get('phase_over_noise'), '{:.2f}')})")))
    return rows


# --------------------------------------------------------------- markdown ---
def plain_summary(agg, cfg=None):
    """A short, readable version of the report, in plain language.

    Every comparison is against something concrete -- the player's own 300 window
    or their own slow-note play -- rather than against invented norms, because
    there are no population figures for any of this.
    """
    cfg = cfg or {}
    fp = agg.get("fingerprint") or {}
    w = agg.get("w300_median", float("nan"))
    grid = agg.get("grid_ms", float("nan"))
    L = []
    A = L.append

    A("## The short version")
    A("")
    verdict = fp.get("verdict", "unknown")
    n_inputs = fp.get("n_inputs", 0)
    s_slow, s_dense = fp.get("share_slow", float("nan")), fp.get("share_dense", float("nan"))
    if verdict == "single input":
        A("You tap with one input — every note comes from the same one, at every "
          "speed. A replay cannot say which device it was.")
    elif s_slow == s_slow and s_dense == s_dense:
        if s_slow < 15 and s_dense > 35:
            A("You tap with one input for almost everything, and bring in a second one "
              "only once the notes get fast. That is what tapping with a pen tip or one "
              "finger looks like: the first input carries the slow notes, the second "
              "appears when the notes outrun it.")
            A("")
            A(f"*{s_slow:.0f}% of your slow notes come from the second input; "
              f"{s_dense:.0f}% of your notes at 1/4 and denser do.*")
        elif s_slow > 35 and s_dense > 35:
            A("You share the tapping between two inputs at every speed — both of them "
              "are used on slow notes and on fast ones alike.")
            A("")
            A(f"*{s_slow:.0f}% of slow notes and {s_dense:.0f}% of notes at 1/4 and "
              "denser come from the second input.*")
        else:
            A("You use two inputs, but unevenly, so the pattern is not one of the "
              "common styles.")
            A("")
            A(f"*{s_slow:.0f}% of slow notes and {s_dense:.0f}% of notes at 1/4 and "
              "denser come from the second input.*")
    A("")
    if agg.get("dense_n", 0) > 0:
        A("**Spread** is how far a tap usually lands from the note; a bigger number "
          "means less consistent.")
        A("")
    if n_inputs > 1 and agg.get("alt_share_of_fast") == agg.get("alt_share_of_fast"):
        share = agg["alt_share_of_fast"]
        if share < 95:
            A(f"*{100 - share:.0f}% of your notes at 1/4 and denser are single-tapped "
              "rather than alternated. Those are counted as fast notes, not as streams, "
              "everywhere below.*")
            A("")

    pts = []
    # 1. the precision cost of alternating
    deg = agg.get("degradation")
    if isinstance(deg, float) and deg == deg:
        dense_sd = agg.get("alt_sd_within", float("nan"))
        sparse_sd = agg.get("sparse_sd_within", float("nan"))
        dr = agg.get("alt_rate", float("nan"))
        sr = agg.get("sparse_rate", float("nan"))
        show_rates = (dr == dr) and (sr == sr)
        if deg >= 1.15:
            line = (f"**Streams are where your accuracy goes.** Your stream taps spread "
                    f"about {dense_sd:.0f} ms either side of the note; slow notes spread "
                    f"about {sparse_sd:.0f} ms. The fast notes are the ones you are least "
                    f"sure of")
            if show_rates:
                line += (f", and it shows in the score: {dr:.0f}% of stream taps land as "
                         f"300s against {sr:.0f}% of slow ones")
            line += (f". The ratio between those two spreads is {deg:.1f}×, which the "
                     "table below calls the precision cost of alternating.")
        elif deg <= 1.0:
            line = (f"**Streams are no harder for you than slow notes.** Your taps "
                    f"spread about {dense_sd:.0f} ms on streams and {sparse_sd:.0f} ms on "
                    f"slow notes — about the same")
            if show_rates:
                line += f", and they score about the same ({dr:.0f}% against {sr:.0f}%)"
            line += "."
        else:
            line = (f"**Streams cost you a little.** Your taps spread about "
                    f"{dense_sd:.0f} ms on streams against {sparse_sd:.0f} ms on slow "
                    f"notes, {deg:.1f}× as wide")
            if show_rates:
                line += (f" — {dr:.0f}% of stream taps are 300s against {sr:.0f}% of slow "
                         f"ones")
            line += "."
        pts.append(line)

    # 2. where streams go wrong
    e15 = agg.get("entry_off15")
    if isinstance(e15, float) and e15 == e15 and agg.get("n_entry", 0) >= 10:
        e30 = agg.get("entry_off30", float("nan"))
        # say the thresholds in milliseconds: "15% off the grid" means nothing
        # on its own, "11 ms out" means something
        gap15 = (f"by more than {0.15 * grid:.0f} ms" if grid == grid
                 else "by more than a fifth of the gap between notes")
        gap30 = (f"{0.30 * grid:.0f} ms" if grid == grid else "twice that")
        er = agg.get("run_pos_early_rate")
        dr = agg.get("run_pos_deep_rate")
        en = agg.get("run_pos_early_n", 0)
        dn = agg.get("run_pos_deep_n", 0)
        deep_known = (er == er and dr == dr and en >= 100 and dn >= 100)
        worsens = deep_known and dr <= er - 2.0
        if e15 >= 35:
            head = ("**Your fast runs start off the beat and get worse from there.**"
                    if worsens else "**Your fast runs start off the beat, and stay there.**")
            line = (f"{head} When a run begins, the first two notes are usually mistimed: "
                    f"in {e15:.0f}% of your runs the gap between those two taps is off "
                    f"{gap15}, and in {e30:.0f}% it is off by {gap30} or more. ")
        else:
            line = (f"**Your fast runs mostly start on the beat.** The first two taps are "
                    f"off {gap15} in {e15:.0f}% of your runs, and off by {gap30} or "
                    f"more in {e30:.0f}% — the rest begin on time. ")

        if deep_known:
            if worsens:
                bands = agg.get("by_run_pos") or []
                b57 = next((r for r in bands if r["band"] == "5-7"), None)
                b812 = next((r for r in bands if r["band"] == "8-12"), None)
                line += (f"Deeper in it dips: {er:.0f}% of the first four notes of a run "
                         f"score 300")
                if b57:
                    line += f" against {b57['rate300']:.0f}% on the fifth to the seventh"
                if b812 and b812["rate300"] >= er - 1.0:
                    line += (f", and the eighth to the twelfth is back at "
                             f"{b812['rate300']:.0f}%, so this is a dip rather than a steady "
                             f"slide")
                line += (" — part of the loss is the run going on, not only how it enters.")
            else:
                line += (f"Deeper in it does not get worse: {er:.0f}% of the first four "
                         f"notes of a run score 300 against {dr:.0f}% from the fifth to the "
                         f"twelfth, so the beat is not there to begin with rather than "
                         f"being lost partway through.")
            tail = agg.get("by_run_pos") or []
            far = next((r for r in tail if r["band"] == "13+"), None)
            if far and far["n"] < 200:
                line += (f" Past the twelfth note {far['rate300']:.0f}% score 300, but only "
                         f"{nfmt(far['n'])} taps get that far, which is too few to lean on.")
        else:
            line += ("Whether it degrades deeper into a run cannot be told from this "
                     "sample: too few taps reach past the fourth note.")
        pts.append(line)

    # 3. the two inputs against each other
    if agg.get("n_alt_pairs", 0) >= 20:
        ph, no = agg.get("phase_ms", float("nan")), agg.get("noise_ms", float("nan"))
        ratio = agg.get("phase_over_noise", float("nan"))
        off = agg.get("hand_offset_systematic_ms", float("nan"))
        if ratio == ratio and ph == ph:
            if ratio >= 1.0:
                line = (f"**Your two inputs are not in time with each other.** On a "
                        f"fast run they land about {ph:.0f} ms apart, while each one on its "
                        f"own only strays about {no:.0f} ms from where it usually is. So "
                        f"the two are further apart from each other than either is from "
                        f"itself — it is the switching between them that is loose, not "
                        f"the taps themselves.")
            elif ratio >= 0.6:
                line = (f"**Your two inputs are close, but not quite locked.** On a "
                        f"fast run they land about {ph:.0f} ms apart, against about "
                        f"{no:.0f} ms of stray in each one on its own. Near enough to "
                        f"moving as a single unit, but not there.")
            else:
                line = (f"**Your two inputs move as one.** They land about {ph:.0f} ms "
                        f"apart while each strays about {no:.0f} ms on its own, so the "
                        f"switching between them has very little left to explain.")
            if off == off and ph == ph and ph > 0:
                share = abs(off) / ph if ph else 0
                if share > 0.5:
                    line += (" Most of that gap is a fixed lag — one input is "
                             "consistently ahead of the other.")
                elif abs(off) >= 0.5:
                    line += (f" About {abs(off):.0f} ms of it is a fixed lag; the rest "
                             f"changes from pair to pair.")
                else:
                    line += (" None of it is a fixed lag: one input is not consistently "
                             "ahead of the other, so there is nothing constant to adjust.")
        pts.append(line)

    # 4. speed
    fastest, ceiling, knee = (agg.get("speed_fastest"), agg.get("speed_ceiling"),
                              agg.get("speed_knee"))
    if fastest:
        fs = (fastest["lo"] + fastest["hi"]) / 2
        sr = agg.get("sparse_rate", float("nan"))
        line = (f"**How fast you can go.** Your fastest streams run at about {fs:.0f} "
                f"BPM, where {fastest['rate300']:.0f}% of the notes land as 300s")
        if sr == sr:
            line += f", against {sr:.0f}% on slow notes"
        if knee:
            ks = (knee["lo"] + knee["hi"]) / 2
            line += f". The slippage starts from about {ks:.0f} BPM"
        else:
            line += (f". At every speed you played, accuracy stays within "
                     f"{cfg.get('drop_pp', 5):.0f} points of your slow-note level")
        line += "."
        pts.append(line)

    # 5. timing
    bias = agg.get("bias")
    if isinstance(bias, float) and bias == bias and abs(bias) >= 2.0:
        where = "early" if bias < 0 else "late"
        gain = agg.get("bias_correction_gain", float("nan"))
        line = (f"**You tap a little {where}.** Your taps land about {abs(bias):.0f} ms "
                f"{where} on average")
        if gain == gain:
            line += (f", and centring them would be worth roughly {gain:+.1f} accuracy "
                     f"points. That is arithmetic on your own numbers, not a promise.")
        else:
            line += "."
        pts.append(line)

    # 6. aim
    lost = (agg.get("aim_miss_offcircle") or 0) + (agg.get("aim_miss_nopress") or 0)
    if lost:
        line = (f"**{nfmt(lost)} notes were lost outright**, {_num(agg.get('aim_miss_pct'), '{:.1f}%')} "
                f"of every note played. {_num(agg.get('aim_miss_offcircle_pct'), '{:.0f}%')} of those "
                f"had a press inside the timing window with the cursor off the circle, which the "
                f"client ignores, and the rest were never pressed at all")
        if agg.get("presses_locked"):
            line += (f". Note lock refused {nfmt(agg.get('presses_locked'))} presses, "
                     f"{_num(agg.get('presses_locked_pct'), '{:.2f}%')} of the total")
        line += "."
        mv, st_ = agg.get("moving_rate300"), agg.get("stationary_rate300")
        if mv == mv and st_ == st_ and agg.get("moving_n", 0) >= 100:
            if abs(mv - st_) >= 1.0:
                line += (f" Taps struck while the cursor was still moving scored {mv:.1f}% "
                         f"against {st_:.1f}% on a settled cursor, so aiming is costing you timing.")
            else:
                line += (f" Taps struck while the cursor was still moving scored {mv:.1f}% "
                         f"against {st_:.1f}% on a settled one, so aiming is not costing you timing.")
        pts.append(line)

    if pts:
        for p in pts:
            A(p)
            A("")
    if agg.get("n_plays", 0) < 3 or agg.get("n_taps", 0) < 800:
        A("> **Small sample.** The numbers below come from too few replays to be "
          "stable; treat the speed ceiling and the hand-to-hand figures as a first "
          "impression rather than a measurement. Twenty or more replays in the same "
          "star range give a much firmer answer.")
        A("")
    A("The sections below give the detail and explain each number. If you read only "
      "one, read **Speed**: it answers how fast you can go before accuracy drops, "
      "and it is the number that changes most as you improve.")
    A("")
    return L


def markdown(agg, plays, charts=None, cfg=None):
    charts = charts or {}
    cfg = cfg or {}
    names = agg.get("usernames") or ["unknown"]
    title = names[0] if len(names) == 1 else ", ".join(names)
    L = []
    A = L.append

    A(f"# osu! replay report — {title}")
    A("")
    A(f"*{nfmt(agg['n_taps'])} taps over {nfmt(agg['n_plays'])} replays · "
      f"{agg.get('date_first') or '?'} to {agg.get('date_last') or '?'} · osu!standard · "
      f"generated {datetime.now():%Y-%m-%d}*")
    A("")
    L.extend(plain_summary(agg, cfg))
    A("## At a glance")
    A("")
    A("| | |")
    A("|:--|:--|")
    for k, v in headline_rows(agg, cfg):
        A(f"| **{k}** | {v} |")
    A("")

    # ---- playstyle
    fp = agg.get("fingerprint") or {}
    A("## 1. Playstyle")
    A("")
    A(f"**{fp.get('verdict', 'unknown')}.** {fp.get('detail', '')}")
    A("")
    A("A replay cannot say which device fired a tap — the game folds keyboard keys and "
      "mouse buttons into the same two actions — but it does say how many inputs were "
      "used and how they are spread across note densities, which is what separates the "
      "styles. The share on the second input, by spacing:")
    A("")
    rows = [(r["label"], nfmt(r["n"]), _num(r["rate300"], "{:.1f}%"),
             _num(r["second_input_share"], "{:.1f}%"),
             _num(r.get("eff_bpm_median"), "{:.0f}")) for r in agg.get("by_divisor", [])]
    A(table(["spacing", "taps", "300-rate", "2nd input", "median eff. BPM"], rows))
    if charts.get("inputs"):
        A(f"![share on the second input by note density]({charts['inputs']})")
        A("")

    # ---- speed
    A("## 2. Speed")
    A("")
    rows = []
    min_taps = cfg.get("ceil_min_taps", 100)
    for r in agg.get("by_bpm_stream", []):
        mix = ", ".join(f"1/{k}" for k in sorted(r["divisor_mix"]))
        taps = nfmt(r["n"]) + ("" if r["n"] >= min_taps else " (thin)")
        rows.append((f"{r['lo']:.0f}–{r['hi']:.0f}", _nps((r["lo"] + r["hi"]) / 2), mix,
                     taps, _num(r.get("alt_pct"), "{:.0f}%"),
                     _num(r["rate300"], "{:.1f}%")))
    if rows:
        A("**1/4 notes and denser** — the speeds that need two inputs. The alternated "
          "column is the share of those taps where the input changed from the note before:")
        A("")
        A(table(["eff. BPM", "notes/s", "spacing", "taps", "alternated", "300-rate"], rows))
        A("")
        if agg.get("speed_basis") == "alternated stream taps":
            A("The held-to figure and the drop point are read from alternated taps only. A "
              "slow bucket played with one input is single tapping, and counting it as "
              "stream play would flatter both.")
            A("")
    bar = cfg.get("ceil_300", 90)
    min_n = nfmt(cfg.get("ceil_min_taps", 100))
    c = agg.get("speed_ceiling")
    f_ = agg.get("speed_fastest")
    named = None
    if c:
        named = c
        A(f"- **Reaches the {bar:.0f}% bar at:** **{_bpm_band(c)}** at {c['rate300']:.1f}% "
          f"on {nfmt(c['n'])} taps (minimum {min_n} taps).")
    elif f_ and f_["rate300"] >= bar - 0.2:
        named = f_
        A(f"- **Just under the {bar:.0f}% bar:** {_bpm_band(f_)} at "
          f"{f_['rate300']:.2f}% on {nfmt(f_['n'])} taps.")
    elif f_:
        named = f_
        A(f"- **Never reaches the {bar:.0f}% bar:** the best is {_bpm_band(f_)} at "
          f"{f_['rate300']:.1f}% on {nfmt(f_['n'])} taps.")
    else:
        A(f"- **Never reaches the {bar:.0f}% bar:** no bucket with at least {min_n} taps.")
    if f_ and (named is None or f_["lo"] != named["lo"]):
        A(f"- **Fastest measured:** {_bpm_band(f_)} ({_nps((f_['lo'] + f_['hi']) / 2)} "
          f"notes/s) at {f_['rate300']:.1f}% on {nfmt(f_['n'])} taps.")
    knee = agg.get("speed_knee")
    if knee:
        A(f"- **Falls below it from:** {_bpm_band(knee)} at {knee['rate300']:.1f}% on "
          f"{nfmt(knee['n'])} taps.")
    A(f"- **Reference level:** {_num(agg.get('sparse_rate'), '{:.1f}')}% on 1/1–1/2 notes "
      f"({nfmt(agg.get('sparse_n', 0))} taps). Every judgement below is against this, not "
      "against a fixed standard.")
    if agg.get("speed_knee"):
        k = agg["speed_knee"]
        A(f"- **First drop:** {_bpm_band(k)} at {k['rate300']:.1f}%, which is more than "
          f"{cfg.get('drop_pp', 5):.0f} points below the "
          f"{_num(agg.get('sparse_rate'), '{:.1f}')}% scored on 1/1–1/2 notes.")
    else:
        A(f"- **First drop:** none. 1/4 accuracy stays within "
          f"{cfg.get('drop_pp', 5):.0f} points of the "
          f"{_num(agg.get('sparse_rate'), '{:.1f}')}% scored on the slower notes.")
    A(f"- **Precision cost of alternating:** {_num(agg.get('degradation'), '{:.2f}')}× — "
      f"tap scatter on 1/4+ notes ({_num(agg.get('dense_sd_within'), '{:.1f}')} ms) against "
      f"1/1–1/2 notes ({_num(agg.get('sparse_sd_within'), '{:.1f}')} ms). Below 1 means "
      "the denser notes are *steadier* than the slow ones.")
    A("")
    A("> **What \"precision cost\" means.** It is a ratio of your own numbers: how "
      "widely your taps scatter on 1/4-and-denser notes, divided by how widely they "
      "scatter on 1/2-and-slower notes, measured inside the same replays. **1.00 means "
      "density costs you nothing extra**; 1.60 means the dense notes are 60% wider. It "
      "is deliberately a within-player figure, so it does not move with how good you "
      "are — a strong player and a weak player can both score 1.5 — and it is why this "
      "report decides it from scatter rather than from accuracy: once most taps land "
      "inside the window, the accuracy figure stops resolving differences that are "
      "still there in the spread.")
    A("")
    if charts.get("speed"):
        A(f"![accuracy against effective BPM]({charts['speed']})")
        A("")
        A("*Numbers under the points are taps in that bucket. The dashed line is the "
          "player's own accuracy on the slower notes.*")
        A("")

    # ---- timing
    A("## 3. Timing")
    A("")
    rows = [
        ("mean error", f"{_num(agg.get('bias'), '{:+.1f}')} ms",
         "negative = early"),
        ("median error", f"{_num(agg.get('median'), '{:+.1f}')} ms", ""),
        ("scatter (own bias removed)", f"{_num(agg.get('jitter_sd_within'), '{:.1f}')} ms",
         "how wide the errors are around the player's own centre"),
        ("taps late", _num(agg.get("late_pct"), "{:.1f}%"), ""),
        ("median 300 window", f"±{_num(agg.get('w300_median'), '{:.1f}')} ms",
         "from each map's own OD and mods"),
        ("centring the errors would be worth",
         f"{_num(agg.get('bias_correction_gain'), '{:+.2f}')} points of 300-rate",
         "arithmetic, not a promise"),
    ]
    A(table(["quantity", "value", "note"], rows, ["left", "right", "left"]))
    if charts.get("errors"):
        A(f"![tap error distribution]({charts['errors']})")
        A("")

    # ---- inputs
    A("## 4. The two inputs")
    A("")
    rows = []
    for r in agg.get("inputs", []):
        rows.append((r["name"], nfmt(r["n"]), _num(r["share"], "{:.1f}%"),
                     _num(r["share_slow"], "{:.0f}%"), _num(r["share_dense"], "{:.0f}%"),
                     _num(r["bias"], "{:+.1f}"), _num(r["sd"], "{:.1f}"),
                     _num(r["rate300"], "{:.1f}%")))
    if rows:
        A(table(["input", "taps", "share", "on 1/1–1/2", "on 1/4+", "bias ms", "scatter ms",
                 "300-rate"], rows))
    else:
        A("Only one input appears in these replays.")
    A("")
    if agg.get("n_alt_pairs"):
        A("**Hand-to-hand timing.** Inside strictly alternating stretches, consecutive "
          "interval errors split into a part the two inputs share and a part where they "
          "disagree. The disagreeing part, with each input's own jitter removed:")
        A("")
        A(table(["quantity", "value", "meaning"], [
            ("hand-to-hand separation", f"{_num(agg.get('phase_ms'), '{:.1f}')} ms",
             "how far apart the two inputs land"),
            ("own jitter floor", f"{_num(agg.get('noise_ms'), '{:.1f}')} ms",
             "how far one input lands from itself"),
            ("ratio", f"{_num(agg.get('phase_over_noise'), '{:.2f}')}",
             "above 1 means the inputs disagree more than they each wobble"),
            ("consistent offset", ("≈0 ms" if (agg.get("hand_offset_systematic_ms") == agg.get("hand_offset_systematic_ms")
                                              and abs(agg.get("hand_offset_systematic_ms") or 0) < 0.05)
                                  else f"{_num(agg.get('hand_offset_systematic_ms'), '{:+.1f}')} ms"),
             "a fixed lag of one input behind the other, if any"),
            ("random part", f"{_num(agg.get('phase_random_ms'), '{:.1f}')} ms",
             "the part that varies from pair to pair"),
        ], ["left", "right", "left"]))
        A(f"*Measured on {nfmt(agg.get('n_alt_pairs', 0))} alternating pairs in "
          f"{nfmt(agg.get('n_runs', 0))} runs.*")
        A("")
        A("> **What these mean, and why they are separate.** The separation and the "
          "jitter floor answer two different questions, and the comparison between "
          "them is the point. If your two inputs land no further apart than each one "
          "lands from itself, they are moving as a single unit and there is nothing to "
          "keep in time. The **consistent offset** answers a further question: whether "
          "that disagreement is a fixed lag that a timing adjustment could remove, or "
          "something that changes every pair. A large offset with a small random part "
          "means one input reliably runs ahead of the other. A small offset with a "
          "large random part means the two are not reliably out of step but unreliably "
          "*in* step — and no single adjustment fixes that, because there is no fixed "
          "amount to adjust by.")
        A("")

    # ---- streams
    A("## 5. Inside fast runs")
    A("")
    rows = [
        ("runs found", nfmt(agg.get("n_runs", 0)), "1/4-or-denser sequences, two notes or longer"),
        ("intervals on the grid", _num(agg.get("on_grid15"), "{:.1f}%"),
         "press intervals within ±15% of the note spacing"),
        ("intervals badly off", _num(agg.get("off_grid50"), "{:.1f}%"),
         "more than 50% out — a note effectively dropped"),
        ("pairs re-pressed", _num(agg.get("near_sim_pct"), "{:.1f}%"),
         "two presses under 25 ms apart"),
        ("strict alternation", _num(agg.get("alternation_pct"), "{:.1f}%"),
         "consecutive taps never on the same input — style-dependent, so read it "
         "against the playstyle above"),
        ("first interval >15% out", _num(agg.get("entry_off15"), "{:.1f}%"),
         "how often a run is already off the beat at its first pair"),
        ("first interval >30% out", _num(agg.get("entry_off30"), "{:.1f}%"), ""),
    ]
    A(table(["quantity", "value", "meaning"], rows, ["left", "right", "left"]))
    A("")
    A("> **What the first-interval figures mean.** They check whether a run begins "
      "on the beat or is already broken before it gets going. These two patterns look "
      "very different here. A player who starts cleanly and then drifts shows a low "
      "figure at the first pair with worse numbers deeper into the run. A player whose "
      "beat never establishes shows a high figure at the very first pair that does not "
      "then get worse. The run-length table below separates them: a deficit at 2 notes "
      "is about the alternating pair itself, while one that keeps growing through "
      "21-40 and 41-80 is about holding on.")
    A("")
    rl = [r for r in agg.get("by_run_len", []) if r["n"] >= 10]
    if rl:
        A("**Accuracy by how long the unbroken run is.** A deficit at 2 notes is the "
          "alternating pair itself; one that grows with length is endurance. Only "
          "compare rows with similar spacing — longer runs sit on slower maps:")
        A("")
        A(table(["run length", "taps", "300-rate", "median spacing"],
                [(f"{r['bucket']} notes", nfmt(r["n"]), _num(r["rate300"], "{:.1f}%"),
                  _num(r.get("spacing_ms"), "{:.0f} ms")) for r in rl]))
    if charts.get("runlen"):
        A(f"![accuracy by run length]({charts['runlen']})")
        A("")
    bp = [r for r in agg.get("by_run_pos", []) if r["n"] >= 20]
    if len(bp) > 1:
        A("**Accuracy by position inside the run.** Length buckets are confounded by which "
          "map a run came from; this asks the narrower question of whether a run degrades "
          "as it goes on, with the same runs counted at each position:")
        A("")
        A(table(["notes into the run", "taps", "300-rate", "mean abs error"],
                [(r["band"], nfmt(r["n"]), _num(r["rate300"], "{:.1f}%"),
                  _num(r["abs_err"], "{:.1f} ms")) for r in bp]))
        A("")
        A(f"*{_num(agg.get('in_run_pct'), '{:.0f}%')} of taps sit inside a run of two or "
          f"more notes; the rest are isolated or on slower spacing.*")
        A("")

    # ---- aim
    A("## 6. Aim, and what it costs")
    A("")
    A("The client only accepts a press when the cursor is inside the circle, so a note "
      "can be lost because the tap was mistimed or because the cursor was not there. "
      "Both are counted, and every note that never scored has one of those two causes:")
    A("")
    aim_rows = [
        ("notes accepted", nfmt(agg.get("aim_judged", 0)),
         "presses the client acted on"),
        ("lost to aim", nfmt(agg.get("aim_miss_offcircle", 0)),
         "a press arrived inside the timing window but the cursor was outside the circle, "
         "so the client ignored it"),
        ("lost with no press", nfmt(agg.get("aim_miss_nopress", 0)),
         "no press anywhere near the note"),
        ("presses thrown away for position",
         f"{nfmt(agg.get('presses_offcircle', 0))} "
         f"({_num(agg.get('presses_offcircle_pct'), '{:.1f}%')} of all presses)",
         "share of every press that produced nothing because of where the cursor was"),
    ]
    A(table(["quantity", "value", "meaning"], aim_rows, ["left", "right", "left"]))
    A("")
    A(f"Of the {nfmt((agg.get('aim_miss_offcircle', 0) or 0) + (agg.get('aim_miss_nopress', 0) or 0))} "
      f"notes that never scored, {_num(agg.get('aim_miss_offcircle_pct'), '{:.0f}%')} "
      f"had a press that was ignored for position and the rest were never pressed.")
    if agg.get("aim_free_notes"):
        A(f"{nfmt(agg['aim_free_notes'])} notes "
          f"({_num(agg.get('aim_free_pct'), '{:.1f}%')}) sit at the same position as the note "
          f"before them — stacked or doubled circles — so reaching them takes no cursor "
          f"movement. They are left out of the arrival and travel figures below.")
        A("")
    if agg.get("presses_locked"):
        A(f"{nfmt(agg.get('presses_locked'))} presses "
          f"({_num(agg.get('presses_locked_pct'), '{:.2f}%')}) were refused by note lock: "
          f"they arrived before the previous circle had been hit, so the client ignored them.")
    A("")
    A("**When the cursor arrives.** Arrival is the moment the cursor last crossed into "
      "the circle before the press, solved by interpolating between the two frames either "
      "side of that crossing. A negative figure means the pen was already waiting; a "
      "positive one means it was still on its way at the note's own time.")
    A("")
    A(table(["quantity", "value", "meaning"], [
        ("median arrival", f"{_num(agg.get('arrival_median_ms'), '{:+.0f}')} ms",
         "negative means the cursor was in place before the note"),
        ("arrived after the note", _num(agg.get("arrived_late_pct"), "{:.1f}%"),
         "notes where the cursor was still on its way at the note's time"),
    ], ["left", "right", "left"]))
    A("")
    A("**Was the pen still travelling when it tapped?** Every accepted press is split by "
      "whether the cursor was moving more than 0.5 osu!pixels per millisecond at the "
      "press, measured between the cursor positions interpolated 8 ms either side of it. "
      "Same notes, same speeds, sorted by whether the hand was still carrying the cursor "
      "at the moment of the tap:")
    A("")
    A(table(["at the press", "taps", "300-rate", "mean abs error"], [
        ("still travelling", nfmt(agg.get("moving_n", 0)),
         _num(agg.get("moving_rate300"), "{:.1f}%"), _num(agg.get("moving_abs_err"), "{:.1f} ms")),
        ("cursor settled", nfmt(agg.get("stationary_n", 0)),
         _num(agg.get("stationary_rate300"), "{:.1f}%"), _num(agg.get("stationary_abs_err"), "{:.1f} ms")),
    ], ["left", "right", "right", "right"]))
    A("")
    by_in = agg.get("aim_by_input") or []
    if len(by_in) > 1:
        A("**Which input pays.** The same split by whichever input produced the press:")
        A("")
        A(table(["input", "taps accepted", "300-rate", "arrived after the note",
                 "presses lost to position"],
                [(r["name"], nfmt(r["judged"]), _num(r["rate300"], "{:.1f}%"),
                  _num(r["late_pct"], "{:.1f}%"), nfmt(r["offcircle_presses"])) for r in by_in]))
        A("")
    pat = [r for r in (agg.get("pattern_by_class") or []) if r["n"]]
    if len(pat) > 1:
        A("**How stream patterns were played, and how they scored.** Counted as stream "
          "play here: 1/4-or-denser, inside a run, with the input changing on every note. "
          "They are separated in circle diameters, and the same spacing played with one "
          "input gets its own rows:")
        A("")
        A(table(["how it was played", "notes", "300-rate", "pen share", "300-rate, pen only"],
                [(r["pattern"] + (" *" if r["n"] < 50 else ""), nfmt(r["n"]),
                  _num(r["rate300"], "{:.1f}%"), _num(r["pen_pct"], "{:.0f}%"),
                  _num(r.get("rate300_pen"), "{:.1f}%")) for r in pat]))
        A("")
        A("*Rows marked with an asterisk carry fewer than 50 notes and should be read as a "
          "hint, not a measurement. A stream is alternated by definition here, so the same "
          "spacing played with one input is listed separately rather than counted as a "
          "stream.*")
        A("")
    sep = [r for r in (agg.get("pattern_by_sep") or []) if r["n"] >= 20]
    if len(sep) > 1:
        A("**Accuracy against how far apart the circles are**, in circle diameters, "
          "measured from the previous note. 1.0 is the point where the circles just stop "
          "overlapping; this table ignores technique, so it mixes alternating and "
          "single-tapped notes and answers only how spacing relates to accuracy:")
        A("")
        A(table(["separation", "notes", "300-rate", "mean abs error"],
                [(f"{r['band']} circles", nfmt(r["n"]), _num(r["rate300"], "{:.1f}%"),
                  _num(r["abs_err"], "{:.1f} ms")) for r in sep]))
        A("")
        base = agg.get("pattern_sep_baseline")
        knee = agg.get("pattern_sep_knee")
        wide_rate = agg.get("pattern_sep_widest_rate")
        wide_band = agg.get("pattern_sep_widest")
        if knee:
            A(f"Accuracy holds at {_num(base, '{:.1f}%')} out to "
              f"{agg.get('pattern_sep_baseline_band')} circle diameters, then falls away: "
              f"{_num(agg.get('pattern_sep_knee_rate'), '{:.1f}%')} at {knee}.")
        elif wide_rate == wide_rate and base == base and wide_rate >= base + 2.0:
            A(f"Accuracy rises with separation rather than falling: {_num(base, '{:.1f}%')} "
              f"at {agg.get('pattern_sep_baseline_band')} circle diameters against "
              f"{_num(wide_rate, '{:.1f}%')} at {wide_band}. Notes further apart score higher "
              f"here than notes close together, not lower.")
        else:
            A(f"Accuracy is flat across separations: {_num(base, '{:.1f}%')} at "
              f"{agg.get('pattern_sep_baseline_band')} circle diameters and "
              f"{_num(wide_rate, '{:.1f}%')} at {wide_band}.")
        A("")
    A("> **What this section is for.** Timing error and aim error are not independent: a "
      "note the cursor never reaches is lost whatever the tap did, and a tap landing while "
      "the hand is still moving is a different act from a tap on a settled cursor. The two "
      "tables above separate them. If the 300-rate is flat between travelling and settled, "
      "aim is not taxing the timing and the deficit is pure tap timing. If travelling taps "
      "lose accuracy, the loss attributed to 'timing' includes the cost of aiming at the "
      "same moment, and practising the tap alone cannot recover it. The position losses "
      "say the same thing more bluntly: those notes never had a chance to be timed well or "
      "badly.")
    A("")

    # ---- maps
    A("## 7. Maps analysed")
    A("")
    rows = []
    for p in sorted(plays, key=lambda x: (-(x.stars if x.stars == x.stars else 0))):
        rows.append((f"{p.artist} – {p.title}", p.version, _num(p.stars, "{:.2f}"),
                     p.mods_str, f"{p.speed:.2f}×", nfmt(len(p.err)),
                     _num(p.acc_reported, "{:.2f}%"),
                     f"[{p.beatmap_id}]({p.url})" if p.url else "—"))
    A(table(["map", "difficulty", "★", "mods", "speed", "taps", "acc", "link"], rows))
    A("")
    A(f"Star ratings come from the public mirrors *today*, not from the version played, "
      "and a replay does not carry one. Mods marked with a speed above 1.00 were "
      "converted back to real-time milliseconds before anything was measured.")
    A("")

    A("## Method")
    A("")
    A("Every tap is paired with the note the game judged it against, and the pairing is "
      "checked: the counts the model produces are compared with the 300/100/50/miss "
      "counts the replay itself recorded, and a replay whose counts cannot be reproduced "
      "is reported rather than quietly used. Hit windows come from each map's own OD and "
      "mods. The full definitions, and what replay analysis cannot see, are in "
      "`docs/metrics.md` and `docs/limitations.md`.")
    A("")
    A("*Generated by osu-replay-report. See docs/posting-results.md to share this.*")
    A("")
    return "\n".join(L)


# ------------------------------------------------------------------- html ---
_HTML_CSS = """
:root { color-scheme: light; }
body { margin: 0 auto; max-width: 62rem; padding: 2rem 1.2rem 4rem;
       font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: #1b1b1b; background: #fff; }
h1 { font-size: 1.7rem; margin: 0 0 .2rem; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 .6rem; padding-bottom: .25rem;
     border-bottom: 1px solid #e3e3e3; }
.sub { color: #6b6b6b; font-size: .9rem; margin-bottom: 1.6rem; }
table { border-collapse: collapse; width: 100%; margin: .7rem 0 1rem; font-size: .875rem; }
th, td { text-align: right; padding: .4rem .55rem; border-bottom: 1px solid #ececec;
         vertical-align: top; }
th:first-child, td:first-child { text-align: left; }
th { color: #444; font-weight: 600; border-bottom: 1.5px solid #ddd; white-space: nowrap; }
td.left, th.left { text-align: left; }
.glance td:first-child { width: 12rem; color: #444; }
img { max-width: 100%; height: auto; margin: .5rem 0 .2rem; }
.note { color: #6b6b6b; font-size: .84rem; }
.callout, .warn { background: #f6f8fa; border-left: 3px solid #b9c6d4;
        padding: .7rem .9rem; margin: 1rem 0; font-size: .9rem; color: #2c2c2c; }
.warn { background: #fff6ee; border-left-color: #c1440e; }
code { background: #f5f5f5; padding: .1rem .3rem; border-radius: 3px; font-size: .88em; }
footer { margin-top: 3rem; color: #6b6b6b; font-size: .84rem; border-top: 1px solid #eee;
         padding-top: .8rem; }
"""


def _img_tag(data_url, alt):
    return f'<img alt="{_html.escape(alt)}" src="{data_url}">'


def _data_url(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _md_table_to_html(text):
    """Convert the markdown tables/paragraphs this module emits into HTML."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|") and i + 1 < len(lines) and set(lines[i + 1]) <= set("|:- "):
            head = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].startswith("|"):
                body.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                                 for row in body)
                       + "</tbody></table>")
            continue
        if ln.startswith("| ") and i + 1 < len(lines) and lines[i + 1].startswith("|:"):
            i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _inline(t):
    t = _html.escape(t, quote=False)
    while "**" in t:
        t = t.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    t = t.replace("&nbsp;", "&nbsp;").replace("&amp;nbsp;", "&nbsp;")
    import re
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def _embed_images(body, charts):
    """Replace markdown image references with embedded data URLs.

    The chart name is taken from the referenced file's basename, so the search
    does not depend on the path convention the caller happened to use."""
    import os
    import re

    def repl(m):
        alt, src = m.group(1), m.group(2)
        name = os.path.splitext(os.path.basename(src))[0]
        data = charts.get(name)
        if data:
            return _img_tag(_data_url(data), alt)
        return f'<img alt="{_html.escape(alt)}" src="{_html.escape(src)}">'

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, body)


def html(report_md, charts=None):
    charts = charts or {}
    body = _embed_images(_md_table_to_html(report_md), charts)
    # crude block conversion: headings, warnings, lists, paragraphs
    out, bullets, quotes = [], [], []

    def flush():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()
        if quotes:
            text = " ".join(quotes)
            cls = "warn" if "Small sample" in text else "callout"
            out.append(f'<div class="{cls}">{_inline(text)}</div>')
            quotes.clear()

    for block in body.split("\n"):
        if not block.strip():
            continue
        b = block.strip()
        if b.startswith("- "):
            bullets.append(b[2:])
            continue
        if b.startswith("> "):
            quotes.append(b[2:])
            continue
        flush()
        if b.startswith("### "):
            out.append(f"<h3>{_inline(b[4:])}</h3>")
        elif b.startswith("## "):
            out.append(f"<h2>{_inline(b[3:])}</h2>")
        elif b.startswith("# "):
            out.append(f"<h1>{_inline(b[2:])}</h1>")
        elif b.startswith("<table") or b.startswith("<img"):
            out.append(b)
        elif b.startswith("*") and b.endswith("*") and len(b) > 2:
            out.append(f'<p class="note">{_inline(b)}</p>')
        else:
            out.append(f"<p>{_inline(b)}</p>")
    flush()
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>osu! replay report</title>\n<style>" + _HTML_CSS + "</style></head>\n"
            "<body>\n" + "\n".join(out) + "\n</body></html>\n")
