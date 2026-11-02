# Playstyles, and what a replay can say about yours

## Why the tool infers rather than asks

An `.osr` file records the button state as a bit field. Keyboard keys and mouse
buttons occupy different bits, but the client uses the same two *actions* for
both, so a replay can say **that** two inputs were used and in what order — not
whether either of them was a pen tip, a mouse button or a key.

What a replay can measure is how those inputs are **distributed across note
densities**, and that turns out to separate the common styles cleanly, because
they differ in *when* the second input is needed:

| style | how the second input is used |
|---|---|
| single-tap | not used at all; one input carries everything |
| alternate, one hand (two keys) | both inputs used on nearly every note, at every speed, from 1/1 upwards |
| alternate, two hands (pen tip + key) | the pen carries everything slow; the key appears only once the notes outrun one finger |

So the fingerprint is the share of taps on the second input, split by snap
divisor. The tool reports the numbers and the verdict that follows from them, so
you can disagree with the verdict and still have the data.

## The verdicts

The thresholds are deliberately loose: a whole-file share must be below 15% on
slow notes or above 35% on dense notes to count, so a style has to be clear before
it is named.

| verdict | condition | reads as |
|---|---|---|
| **single input** | only one input in the replay | one-tap style; device not recoverable |
| **alternates on streams only** | <15% on 1/1–1/2 and >35% on 1/4+ | one input carries the slow notes, the other joins for streams |
| **alternates throughout** | >35% on both | taps shared between two inputs at every speed |
| **one input, two available** | <15% on 1/4+ | a second input exists but is barely used in these replays |
| **mixed** | anything else | alternation is partial or inconsistent |

**"Alternates on streams only" is the signature of pen-tip-and-key tapping, but it
does not prove it.** A player using two keyboard keys who only alternates once
notes get dense produces the same fingerprint. The difference between those two
players is mechanical — one of them is tapping with the hand that is also aiming —
and mechanics are exactly what a replay does not record. The report says which
fingerprint was found, not which device was in your hand.

## What applies to which style

| measurement | single-tap | alternates (either kind) |
|---|---|---|
| speed curve, ceiling, knee | yes | yes |
| bias, scatter, centring gain | yes | yes |
| accuracy by run length | yes | yes |
| on the grid, badly off, pairs re-pressed | yes | yes |
| first-interval behaviour | yes | yes |
| per-input bias and scatter | one row | two rows |
| hand-to-hand separation, ratio, offset | **not reported** | yes |
| alternation rate | 0% by definition; not diagnostic | yes |

A single-input player gets no hand-to-hand numbers because there is nothing to
compare — the split requires two inputs firing in alternation. This is not a
missing measurement; the quantity does not exist for that style.

## Reading your own fingerprint

The `by divisor` table is worth looking at directly rather than only reading the
verdict:

* **Second input near 50% from 1/1 upwards** — you alternate on everything,
  including single notes. Common for two-key players.
* **Second input near 0% until 1/4, then jumping up** — you single-tap until the
  notes force you to alternate. This is the style the report was built for, and it
  is also the style where the hand-to-hand numbers matter most.
* **Second input rising but never reaching 50%, and alternation rate below 100%**
  — your alternation is breaking down inside streams: some consecutive taps land
  on the same input. Look at *alternation rate* and *pairs re-pressed* together
  with the run-length table; that combination is a player dropping notes and
  re-pressing the pair, not a player timing badly.
* **A dip at 1/3** — triplets are a different pattern and many players do not
  alternate through them even when they alternate 1/4 fine. The report shows 1/3
  separately for this reason.

## If your style is not listed

Nothing breaks. The fingerprint reports the number of inputs and their
distribution; a style that does not match a named pattern gets **mixed** and the
raw table, which is more useful than a wrong label. Mouse-and-key, one-hand
two-key, two-hand two-key, tap-x and single-tap-with-tapping-all appear as
different distributions and the rest of the report is unaffected.
