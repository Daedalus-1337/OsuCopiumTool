# Method: how a replay becomes numbers

This describes the path from an `.osr` file to the values in a report, and the
checks that let you judge how far to trust them.

---

## 1. Reading the replay

An `.osr` file holds a header and a compressed recording of the play:

| header field | what it is used for |
|---|---|
| game mode | only osu!standard is analysed |
| beatmap MD5 | identifying which `.osu` file the play belongs to |
| player name | the report title |
| mods | hit windows and the speed factor |
| 300 / 100 / 50 / miss counts | **the ground truth the model is checked against** |
| max combo, score | shown for reference, not measured |
| timestamp | date range |
| replay data | LZMA-compressed frames: time delta, x, y, button state |

Frames record the cursor position and the button state. A frame is written when
either changes, so **press and release times are discrete events recorded to the
millisecond**. Tap timing is therefore not limited by the cursor sampling rate;
only the path the cursor took between frames is interpolated, and that affects
aim analysis rather than timing.

The button state is a bit field. Keyboard keys and mouse buttons occupy different
bits, but the client uses the same two *actions* for both, so which bits appear
says how many inputs were used and in what order — not what the inputs physically
were. This is the reason playstyle is inferred rather than read off.

## 2. Finding the beatmap

A replay stores the beatmap's MD5 hash, not its content. The `.osu` file is found
in this order:

1. the local map cache (`--maps`, default `<out>/maps`), compared by MD5;
2. the folders passed with `--songs`, i.e. your osu! install, if you gave any;
3. the public mirrors (`osu.direct`, `catboy.best`), downloaded and cached.

A beatmap that has been edited since the play no longer exists in the form that
was played, and cannot be resolved. Those replays are skipped and listed.

## 3. Judging the taps

Two unknowns have to be resolved before any timing can be measured: which press
belonged to which note, and how the replay's clock relates to the beatmap's.

**Pairing.** A press judges a note when two conditions hold: the press falls inside
the note's 50 window, and the cursor is inside the note's circle at that instant.
Among the notes under the cursor the one nearest in time is taken. A press with the
cursor over no circle judges nothing, and the note it was probably aimed at is
recorded as lost to aim rather than as a timing miss. The client's note lock is
applied as well: a press arriving before the start time of an earlier unjudged
circle is refused. Two presses closer together than 5 ms are treated as one bounce;
the second finds nothing left inside its window and judges nothing.

Circle radius follows `r = 54.4 − 4.48·CS` in osu!pixels, with HR's circle-size
multiplier applied. HR also mirrors the playfield vertically, so note positions are
reflected before any distance is taken; the replay's cursor frames are already in
that space.

**Excluded replays.** A replay played with relax, autoplay, cinema or autopilot
carries input the client produced, not the player, so nothing on it measures
tapping. Those replays are skipped and the reason is reported: relax has the client
pressing, autopilot has it aiming, autoplay and cinema have it playing the map.

**Hit windows** come from each beatmap's own OD and the play's mods:
`300 = 80 − 6·OD`, `100 = 140 − 8·OD`, `50 = 200 − 10·OD`, with HR raising OD and
EZ halving it, and all three divided by the speed factor for DT/HT.

**Clock calibration.** The replay's timestamps and the beatmap's note times are
not guaranteed to be offset by zero — the client's clock, the recording, and the
map's own inheritance can each contribute a constant. The offset is not guessed:
it is chosen as the value that best reproduces **the 300/100/50/miss counts the
replay itself recorded**. The residual (how many judgements are still wrong after
calibration) is reported, and a replay whose counts cannot be reproduced is
reported rather than quietly used. In practice the residual is a handful of notes
out of a thousand.

**Real time.** Lazer writes replay timestamps on the beatmap clock, so a DT play's
timestamps are already in map milliseconds. Every error is converted to wall-clock
milliseconds by dividing by the speed factor, so 150 ms of spacing means 150 ms to
the player regardless of mods. Without this step a DT play looks like it has
tighter timing than it does.

## 4. Labelling each note

Each note is labelled by its spacing from the previous note, as a fraction of a
beat, read from the beatmap's own timing points:

```
divisor = round(beat_length / spacing)
```

The result must land within 15% of a common divisor (`1, 2, 3, 4, 6, 8, 12, 16`)
or the note is left unlabelled. Spacings that are genuinely irregular — the note
after a break, a stack, a pattern mid-transition — are excluded rather than
forced into a bucket.

From the divisor and the local beat length:

```
effective BPM = (60000 / beat_length) × speed × divisor / 4
```

which is the note rate in the convention players use: the BPM the map would need
for the same note rate as 1/4 notes.

**An edge case worth knowing.** A map's first timing point is often not at time
zero — one map in this project's test set starts its timing at 1667 ms. The
beatmap reader returns a 500 ms default for times before the first timing point,
which would silently produce 120 BPM for the opening notes. The metric code
handles this by using the earliest uninherited beat length instead.

## 5. What a run is

A **stream run** is a maximal sequence of consecutive notes at divisor ≥ 4 — a
quarter of a beat apart or denser. This is map-relative on purpose: 1/4 on a slow
map and 1/8 on a half-speed map are both runs, and both need two inputs from any
player who alternates at all.

Runs are what the alternation and phase measurements are computed inside, because
those measurements are only meaningful where the notes are dense enough that one
input cannot carry them.

## 6. Checks you can run

| check | where to find it | what to expect |
|---|---|---|
| count residual | not shown in the report; use `--json` on `inspect-replays.py`, key `count_residual` | small: a few judgements out of the recorded total |
| clock offset | same, key `clock_delta` | a constant per play, a few ms |
| skipped replays | end of the console output, and `summary.json` | each with a reason |
| unlabelled notes | `by_divisor` shares in the report | a small share of taps; if most taps are unlabelled, the timing points are wrong |

A report whose skipped-replay list is long, or whose unlabelled share is high, is
a report about a different set of replays than you thought.

## 7. Known fidelity limits

The pairing model reproduces the recorded judgements closely, but it is a
*reconstruction calibrated against the game's own counts*, not a reimplementation
of the client's judgement code.

- **Stacking is not modelled.** Where the client draws overlapping circles offset
  from one another, this reads them at their file coordinates, so a press inside the
  overlap can be judged against the wrong circle. Dense low-AR patterns are the
  affected case.
- **Timing resolution comes from the client's input tick.** The replay stores whole
  milliseconds, and a press or release forces a frame of its own rather than waiting
  for the 60 Hz cadence applied to cursor movement, so a press is stamped at the
  update that processed it. The remaining uncertainty is one update tick, a few
  milliseconds, plus whatever the input device adds before the client sees it.
- **A held button can judge a slider head without a fresh press.** That path is not
  modelled: a press is paired to the note it lands on.
- **The clock offset is fitted, not known.** It is chosen to reproduce the recorded
  counts, so the bias figure is reliable while the absolute placement of the taps is
  not.

Each replay carries its own residual, so plays that the model fits badly can be
identified rather than averaged in with the rest.
