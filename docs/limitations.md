# Limitations

What a replay cannot show, and where the numbers in a report stop being worth
trusting. Worth reading once before drawing conclusions from a report.

---

## What a replay does not record

**Which device fired a tap.** Keyboard keys and mouse buttons use different bits
in the frame data, but the client drives the same two actions from both, so a
replay cannot say whether an input was a pen tip, a mouse button or a key. The
playstyle verdict is an inference from how the inputs are distributed across note
densities. See `playstyles.md`.

**Anything outside the game.** Monitor refresh and response time, audio output
latency, tablet active area, grip, seat height, how tired you were. All of these
move timing, and none of them are in the file. Two players with identical
hand-to-hand numbers can be playing on very different setups.

**Taps the game threw away.** The replay is the game's own recording of judgements
it already made. A press that the game discarded — a bounce inside the double-tap
filter, a press outside a circle — leaves little or no trace. Very heavy mashing
is therefore measured less well than clean play.

**Anything about aim.** Cursor positions are interpolated between frames, so
movement analysis would be approximate. This tool does not attempt it.

## Where the measurement is approximate

**Pairing taps to notes is a reconstruction.** Notes are matched to presses in
order and the pairing is calibrated so the model reproduces the counts the replay
recorded. It reproduces them closely (a handful of judgements out of a thousand in
the test set), but it is calibrated against the game's counts, not a
reimplementation of the client's judgement code. The current client's rules differ
in two ways that matter for edge cases: it does not lock notes in order, and it
routes a press by cursor position so it must land inside the circle. Those
differences move about 1% of judgements. Plays with deliberate out-of-circle
presses or heavy mashing deserve more caution than clean plays.

**Per-play bias is not identifiable across plays.** A clock offset and a genuine
player bias produce the same signature, so absolute accuracy and evenness are
measured separately and the report says so. Within one play, relative timing is
well determined; across plays, the common offset is not. This is why the scatter
figure shown in the headline removes each play's own bias rather than using the
pooled figure.

**The speed ceiling is a threshold, not a fact.** It depends on `--ceil-300`,
`--ceil-min-taps` and `--bpm-bucket`. Every report states the values it used and
records them in `summary.json`, because two reports generated with different
thresholds are not comparable. The *fastest measured* figure is reported alongside
precisely because a single threshold hides the top end.

**Wilson intervals in a single-player report are indicative.** They treat taps as
independent, which they are not — taps inside one play share a map, a session and
a clock. For comparisons *between* players, use the cluster bootstrap in
`tools/exp_compare.py`, which resamples plays instead, and treat the pooled
figures as the primary result.

## Where the data itself is weak

**Sample size.** A handful of replays cannot establish a player's speed ceiling or
their hand-to-hand timing. The report warns when a set is too small, but the
warning threshold is forgiving: tens of replays in one star range are needed
before the numbers are stable, and hundreds before small differences mean
anything.

**Exported plays are not a random sample.** If you export your best scores, the
report describes your best scores. If you export the last twenty plays you
happened to play, it describes those maps. Neither is wrong, but they answer
different questions and the report cannot tell which you did.

**Star ratings are current, not historical.** A replay carries no star rating. The
rating shown comes from the mirror today, and if the beatmap was edited after the
play the rating belongs to a different version of the map. Beatmaps edited in a way
that changed their identity cannot be resolved at all; those replays are skipped
and listed.

**Difficulty settings come from the file as it exists now**, so a map that was
renerfed after the play is measured against its current OD, not the one played.

**Mods that change speed** are converted back to real time, so DT and HT plays are
comparable to nomod ones. But a DT play is not the same task as a nomod play at
the same note rate: reading, aim and stamina all differ. Comparing a player's DT
stream ceiling with another player's nomod ceiling is not like for like.

**Only osu!standard.** Other modes are skipped, and replays using Relax are
excluded from the research scripts because the game taps for the player and there
is no input to measure.

## What the report deliberately does not do

* **No skill rating, no pp, no rank.** Nothing here estimates how good a player is
  overall. Every comparison is either within a player or between players on
  matched maps and matched accuracy.
* **No comparison to other players, built in.** A single-player report describes
  one person. Comparing two players properly needs matched maps, matched scored
  accuracy and several players per group, which is a study rather than a report —
  `tools/exp_compare.py` is the tool for it.
* **No advice.** The report measures. Which fix follows from a measurement is a
  judgement about a person that a timing distribution cannot make.
