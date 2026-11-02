# Sharing and comparing results

The point of a standard report format is that results from different people can be
put side by side. Two things make that possible: a fixed set of keys in
`summary.json`, and a record of the thresholds each report was generated with, so
reports that used different settings are never compared as if they matched.

---

## What to post

| file | when to use it |
|---|---|
| `report.md` + the `charts/` folder | posting to a forum, a GitHub issue, a Discord message that renders markdown |
| `report.html` | posting a single file — it has the charts embedded and needs no other files |
| `summary.json` | whenever you want the numbers to be collected or compared programmatically |

The one file that always works alone is `report.html`.



* **Your username is in the report.** Every replay header carries it, and it is
  used as the title.
* **Map links are public.** The report links each beatmap by ID.
* **Check the skipped list.** If replays were skipped the report describes fewer
  plays than you exported; the console output and `summary.json` say why.
* **Check the sample warning.** If the report carries the small-sample notice, say
  so when you post, or add more replays first — the numbers will move.

## `summary.json`

```json
{
  "summary": {
    "n_plays": 50, "n_taps": 30572,
    "usernames": ["ExamplePlayer"],
    "date_first": "2025-09-02", "date_last": "2026-10-31",
    "star_min": 5.00, "star_max": 6.86, "star_median": 5.68,
    "map_bpm_min": 85.0, "map_bpm_max": 270.0,

    "acc_300_rate": 95.06, "acc_reported": 96.94,
    "bias": -4.11, "jitter_sd_within": 17.25, "bias_correction_gain": 0.70,

    "dense_rate": 87.22, "sparse_rate": 96.10, "dense_n": 3021, "sparse_n": 23919,
    "degradation": 1.61, "dense_sd_within": 25.40, "sparse_sd_within": 15.79,

    "on_grid15": 49.42, "off_grid50": 12.57, "near_sim_pct": 2.89,
    "alternation_pct": 68.03, "entry_off15": 47.34, "entry_off30": 25.36,
    "n_runs": 1033,

    "phase_ms": 20.48, "noise_ms": 12.99, "phase_over_noise": 1.58,
    "hand_offset_median_ms": 0.0, "n_alt_pairs": 1346,

    "playstyle": "alternates on streams only", "inputs": [1, 2],
    "share_second_input_slow": 0.23, "share_second_input_dense": 35.22,

    "speed_ceiling_bpm": 100.0, "speed_ceiling_rate300": 95.96,
    "speed_ceiling_taps": 643, "speed_knee_bpm": 120.0,
    "speed_plateau_rate300": 96.10,

    "mods": {"NM": 44, "NF": 6},
    "tool": {"name": "osu-replay-report", "version": "1.0",
             "ceil_300": 90.0, "ceil_min_taps": 100}
  },
  "generated": "2026-11-01T23:39:26",
  "replays_dir": "...",
  "skipped": []
}
```

Key notes, because these are where comparisons go wrong:

* **`tool.ceil_300` and `tool.ceil_min_taps` are recorded so you can refuse to
  compare reports generated with different thresholds.** Add them to any table you
  publish.
* **`phase_over_noise` is the only directly comparable timing figure** across
  players of different skill, because it is dimensionless and normalised by each
  player's own jitter. `phase_ms` on its own is not: a more precise player has a
  smaller `phase_ms` for that reason alone.
* **`degradation` is comparable across skill** for the same reason: it is a
  within-player ratio.
* **`speed_ceiling_bpm` is not comparable** between players who played different
  star ranges. Pair it with `star_min`/`star_max` or it means nothing.
* **`alternation_pct` is style-dependent.** A single-tap player scores near 0 by
  definition; do not read it as a defect.
* **Absent keys.** `phase_ms`, `phase_over_noise` and friends are missing for
  single-input players, because the quantity does not exist for them. Cross-player
  tables need to tolerate that.

## Comparing two players properly

A single-player report describes one person. Comparing two people needs more care
than putting two reports side by side, because most of the difference you will see
is general skill rather than anything about style. `tools/exp_compare.py` is the
tool for that. It takes two sets of replays and reports:

* accuracy by spacing band and by unbreakable stream-run length, side by side;
* every headline quantity with a cluster bootstrap over plays, both within each
  player and on the **difference between** them;
* the same thing restricted to a subset, for sensitivity checks.

```bash
python batch.py     replay_folder_A maps out/A
python batch.py     replay_folder_B maps out/B
python tools/exp_compare.py out/A PlayerA out/B PlayerB out/compare
```

What makes such a comparison credible, beyond more replays:

1. **Regularise on scored accuracy.** Restrict both players to plays in the same
   accuracy band, otherwise you are measuring skill.
2. **Same star range.** Otherwise the map difficulty explains the result.
3. **Same maps if you can get them.** The strongest design is both players on the
   same beatmaps, which removes difficulty as an explanation entirely.
4. **Several players per style, not more plays of the same two people.** One player
   per style leaves player and style completely confounded — this is the single
   biggest limitation of any two-player comparison, and no amount of analysis
   fixes it.
5. **Random plays, not best plays.** Exporting your top scores describes your top
   scores. That bias flatters whoever exported theirs.

## Contributing a results file

If you want your numbers included in a shared table, post `summary.json` together
with the exact command you ran and the thresholds it used. The `tool` block in the
file records those, so a collector can group like with like and exclude anything
generated with different settings.

Please include:

* `summary.json`;
* the `report.md` or `report.html`, so the numbers can be checked against the
  replay set they came from;
* what you exported and how — top plays, last N plays, a whole folder — because
  that determines what the numbers describe;
* your playstyle if you know it, and whether the fingerprint in the report agreed.
 
