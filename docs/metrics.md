# Metric definitions

Every quantity the report shows, defined precisely. Units are milliseconds
unless stated. `err` is a tap's error against its own note, negative = early.

---

## Accuracy

**300-rate** — share of taps whose absolute error is inside their own play's 300
window. Each tap uses the window from its own beatmap's OD and mods, never a
fixed constant, so plays of different difficulty are comparable.

**reported accuracy** — the game's own weighted figure from the replay header. It
counts 300s, 100s and 50s differently and includes slider judgements, so it is
*not* the same quantity as the 300-rate and will generally be a few points higher.
Both are shown so the report can be reconciled with what the game told you.

**sparse rate** — 300-rate on notes at 1/2 or slower (divisor ≤ 2). This is the
player's own reference level: what they score when the notes do not require two
inputs.

**dense rate** — 300-rate on notes at 1/4 or denser (divisor ≥ 4).

**stream penalty** — `sparse rate − dense rate`, in percentage points.

## Timing

**bias** — mean of all matched errors. Negative = the player is early.

**median** — median error. Robust to a few badly mistimed taps.

**scatter, pooled** — standard deviation of all errors. Includes any difference
in bias between plays, so it is inflated if the player's timing drifted across
sessions.

**scatter, own bias removed** — the sum of within-play variances pooled across
plays. This is the honest measure of consistency and is the one the report shows
in the headline.

**taps late** — share of errors greater than zero.

**300 window** — the median half-width across the plays analysed, shown so the
scatter can be read against it. A scatter of 15 ms against a ±21 ms window means
most taps land, but the tail does not.

**centring gain** — arithmetic only: the 300-rate the same errors would give if
each play's median error were shifted to zero. It is the size of the prize for
fixing a systematic bias, not a promise that a player can fix it.

## Speed

Notes are labelled by snap divisor from the beatmap's timing points; see
`method.md`. **Effective BPM** = `(60000 / beat_length) × speed × divisor / 4`,
with **notes per second** = effective BPM ÷ 15.

**speed ceiling** — the fastest effective-BPM bucket that still has at least
`--ceil-min-taps` taps and at least `--ceil-300` percent 300s, counting only the
contiguous climb from the slowest bucket. "Contiguous" means a single bad bucket
stops the count, so the number is not set by one lucky fast section.

**speed fastest** — the fastest bucket with enough taps, whatever its accuracy.
Reported alongside the ceiling because a threshold hides the top end: a player
can miss the 90% bar at 200 BPM while still scoring 88% there on a large sample,
which is very different from not being able to play at that speed.

**speed plateau** — the sparse rate, i.e. the player's own baseline.

**speed knee** — the slowest bucket whose 300-rate is more than `--drop-pp` points
below the plateau. If no bucket qualifies, accuracy holds its level. This is the
"BPM where accuracy starts to drop" figure, and it is defined relative to the
player's own baseline rather than an absolute standard, so it means the same thing
for a 60% player and a 98% player.

**precision cost of alternating (degradation)** — within-play scatter on dense
notes divided by within-play scatter on sparse notes, pooled. A within-player
contrast, so it carries no overall skill difference: it asks only how much worse
denser notes make this player, relative to how they play when notes are slow.

Why scatter rather than accuracy, and within-play rather than pooled: a 300-rate
saturates once most taps land, so two players at 96% can differ substantially in
underlying precision. Scatter keeps resolving where the rate cannot. Pooling
across plays would mix in between-session drift; within-play does not.

## The two inputs

**share on the second input** — percentage of taps produced by the input that is
used less on slow notes. Broken down by divisor, it is the playstyle fingerprint.

**per-input bias and scatter** — computed the same way as above, restricted to
that input's taps.

### Hand-to-hand timing

Inside strictly alternating, evenly spaced stretches, consecutive press-interval
errors `delta_i = err_i − err_(i−1)` are split into

```
alt = (delta_i − delta_(i+1)) / 2      what alternates between the inputs
com = (delta_i + delta_(i+1)) / 2      what both inputs share
```

Under independent per-tap jitter the alternating part accumulates **three times**
the variance of the shared part. That factor is not a choice: if each tap carries
independent noise, the alternating combination takes a difference of four noise
terms and the shared one takes a sum of four, giving a 3:1 ratio in variance.
Therefore

```
phase_ms = sqrt(max(0, rms(alt)^2 − 3 rms(com)^2))
noise_ms = rms(com) × sqrt(2)
```

**phase_ms** — the rms disagreement between the two inputs, with each input's own
jitter removed. It is how far apart the two inputs land on the same note.

**noise_ms** — the jitter one input has on its own, i.e. the floor that phase_ms
should be judged against.

**ratio** — `phase_ms / noise_ms`. Above 1 means the inputs disagree with each
other more than either disagrees with itself. Near 0 means they move as one unit.
Being dimensionless, it compares two players without either one's overall
precision setting the scale.

**consistent offset** — the mean of `alt`: a fixed lag of one input behind the
other, if one exists.

**random part** — the standard deviation of `alt`, since
`rms(alt)² = mean(alt)² + variance(alt)`. A large phase_ms made mostly of random
part means the inputs are not reliably out of step but unreliably in step, which
is a different problem from a consistent lag and is not fixed by cueing a timing
offset.

## Inside streams

**on the grid** — share of in-run press intervals within ±15% of the note spacing.
A locked alternation sits here nearly always.

**badly off** — share more than 50% out. These are intervals where a note was
effectively dropped and the pair re-pressed.

**pairs re-pressed** — share of in-run intervals shorter than 25 ms.

**strict alternation** — share of consecutive in-run tap pairs on different
inputs. A single-input player scores 0 here and it means nothing for them.

**first interval >15% / >30% out** — how often a run is already off the beat at
its very first pair. High values mean the beat is never established rather than
gradually lost: if a run starts wrong and stays at that level, the problem is the
entry, not the sustain.

**accuracy by run length** — 300-rate on notes inside runs of 2, 3–5, 6–10,
11–20, 21–40, 41–80 and 81+ notes, each bucket carrying its median note spacing.
A deficit at 2 notes means the problem is in the alternating pair itself; one that
keeps growing through the longer buckets means endurance or drift.

## Aim

Position is read from the replay's own cursor frames and compared with each note's
own coordinates, both in osu!pixels, so no scaling is involved. A press judges a
note only when the cursor is inside that note's circle, which gives every note that
never scored a cause.

**notes accepted** — presses the client acted on.

**lost to aim** — a press fell inside the note's timing window, but the cursor was
outside the circle, so the press judged nothing and the note went unjudged. The
press is real; only where it landed was wrong.

**lost with no press** — no press fell near the note at all.

**presses thrown away for position** — every press the client ignored because the
cursor was over no circle, as a share of all presses. This measures the aim penalty
in presses rather than in notes.

**note lock** — presses refused because an earlier circle was still unjudged. Such
a press does nothing whatever its timing.

**arrival** — when the cursor last crossed into the circle before the press, relative
to the note's time. The search starts at the press where the note was judged and at
the note's own time where it was not, and walks back while the samples stay inside the
circle, so an earlier pass through it does not count as an arrival. The crossing itself
is solved between the last sample outside the circle and the first inside it rather
than taken as a frame time. Negative is early. `arrived after the note` is the share of
notes the cursor reached after their own time. Where the cursor had been inside for
more than a second the entry is earlier than can be measured, and the note is counted
as unmeasurable instead of being given a clamped value.

**still travelling / cursor settled** — accepted presses split at 0.5 osu!pixels per
millisecond of cursor movement at the press, taken as a central difference between the
cursor positions interpolated 8 ms either side of it, each group reported with its
300-rate and mean absolute error. A flat split means aim is not
taxing the timing. A deficit in the travelling group means part of what the timing
tables call tap error is the cost of aiming at the same instant.

**jump** — distance from the previous note's position, bucketed 0–50, 50–100,
100–200 and 200+ osu!pixels, each with 300-rate and mean absolute error.

**stream separation** — distance from the previous note divided by the circle
diameter, so 1.0 is where two circles stop overlapping. Reported for notes inside
streams, in bands from under 0.5 to 2.0 or more diameters, with 300-rate and mean
error for each. The bands pool across maps of any circle size because the unit is
the circle, not the pixel. Accuracy holds at the rate of the tightest separation
played until it drops by 5 points, and the band where that happens is reported as
the spacing at which accuracy falls away.

**how a stream was played** — a note counts as stream play when its snap divisor is
1/4 or denser, a neighbouring note is also that dense, and it was struck with a
different input than the note before it. Those notes are split at one circle
diameter into stacked and spaced streams. The same spacing played with one input is
reported separately as single-tapped, stacked or spaced, so a single-tapped run is
not counted as a stream. Notes at 1/4 without a dense neighbour are isolated fast
notes, and everything slower is a slow note. Each class carries its pen share and
its 300-rate on pen taps, so classes played with different hands can still be
compared.

**no movement required** — notes within 1 osu!pixel of the previous note's position,
which means a stack or a doubled circle. Reaching them asks nothing of the cursor, so
they are excluded from arrival, from the travelling/settled split and from the jump
buckets, and counted separately. They remain in the miss attribution, where the
position of the press is still what decides the outcome.

## Thresholds are conventions

`--ceil-300` (default 90), `--ceil-min-taps` (default 100), `--drop-pp` (default 5)
and `--bpm-bucket` (default 20) decide where the ceiling and the knee land. They
are stated in every report and recorded in `summary.json`, because two reports are
only comparable like for like when these agree.
