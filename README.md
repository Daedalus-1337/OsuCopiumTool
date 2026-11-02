# osu-replay-report
```
Vibe coded tool made with Deepseek V4.1 Flash 
```
I wanted a tool so that I could figure out exactly how I was loosing accuracy on streams so I made this

Turn a folder of osu! replays into a readable report about how you actually time
your taps: how fast you can go before your accuracy drops, whether your two
inputs stay in step with each other, and where in a stream things fall apart.

Everything it measures is reported for any style, and the
report says which style it thinks you are using and why.

```
$ python report.py ~/osu-replays --out my-report
6 replay file(s) in /home/me/osu-replays
analysed 6 replay(s), skipped 0
wrote 4 chart(s)

playstyle: alternates throughout
300-rate 94.5%   bias -4.1 ms   scatter 11.5 ms
held 1/4 streams to 170-190 eff. BPM at 92.9% (1847 taps)

wrote my-report/report.md
wrote my-report/report.html
wrote my-report/summary.json
```

`report.html` is one self-contained file with the charts embedded, so it can be
opened directly or shared as a single attachment.

---

## Install

Python 3.10 or newer.

```bash
git clone <this repo>
cd osu-replay-report
python -m pip install -r requirements.txt     # numpy, matplotlib
```

No API key, no account, no game install needed. Beatmaps are fetched from public
mirrors by their MD5 hash the first time they are needed and cached locally
afterwards; if you have the game installed, pass `--songs` and nothing is
downloaded at all.
```
## uv install instructions 
uv venv 
uv pip install -r requirements.txt
```
## Quickstart

```bash
# 1. put some .osr files in a folder

# 2. generate the report
python report.py /path/to/replays --out my-report
# OR if using UV 
uv run python report.py \replays\mrekk --out my_report  
# with the game installed, resolve beatmaps locally and stay offline
python report.py /path/to/replays --songs "/path/to/osu!/Songs" --out my-report
```

Open `my-report/report.html`, or read `my-report/report.md` if you want to post
it somewhere that renders markdown.

### Where replays come from

* **osu!lazer** — right-click a score in the song select or results screen and
  export the replay, or use a tool such as
  [BeatmapExporter](https://github.com/kabiiQ/BeatmapExporter) to export many at
  once. Exported `.osr` files can be dropped straight into a folder.
* **osu!stable / the website** — replays can be downloaded from a score's page on
  osu.ppy.sh.

Twenty or more replays in the same star range give a much firmer answer than two
or three. The report warns you when the sample is too small to lean on.

## What the report gives you

| section | answers |
|---|---|
| **The short version** | the findings in plain language, with the numbers that matter and what each one means for you |
| **At a glance** | the headline figures in one table: style, star and BPM range, accuracy, timing, speed, where accuracy drops |
| **1. Playstyle** | how many inputs you use and how they are spread across note densities — which is what separates single-tap, alternation and pen-plus-key styles |
| **2. Speed** | accuracy against **effective BPM**, the speed held before accuracy drops, and the precision cost of alternating |
| **3. Timing** | systematic bias (early/late) against scatter, with your error distribution against the actual 300 window |
| **4. The two inputs** | each input's own accuracy and bias, and the hand-to-hand timing separation between them |
| **5. Inside streams** | intervals on the grid, notes effectively dropped, alternation rate, how a run's very first pair behaves, and accuracy by run length |
| **6. Maps analysed** | every beatmap with star rating, mods, note count, accuracy and a link |

Three figures carry a "what this means" note in the report itself, because they
are the ones most often misread: the **precision cost of alternating**, the
**first-interval** figures, and the **consistent offset** in the hand-to-hand
table. Each note says what the number is a ratio *of*, and what it would mean at
other values.

Also written: `summary.json`, a fixed-key digest of the headline numbers intended
for collecting results across players. See `docs/posting-results.md`.

## Concepts

**Snap divisor.** Every note is labelled by how much of a beat it was spaced from
the previous note — `1/1`, `1/2`, `1/4`, `1/8` — read from the beatmap's own
timing points. 

**Effective BPM.** The BPM
the map would need for the same note rate as 1/4 notes. A 1/8 stream on a 96 BPM
map is **192 effective BPM**. Notes per second is effective BPM ÷ 15. This makes
speeds comparable across maps, mods and snap divisors.

**Stream run.** A maximal sequence of consecutive notes at `1/4` or denser. On a
slow map `1/4` qualifies; on a fast map `1/8` does too.

**Hand-to-hand separation.** Inside strictly alternating stretches, the press
intervals are split into a part both inputs share and a part where they disagree.
The disagreeing part, with each input's own jitter removed, is how far apart the
two inputs land. Divided by how much each input wobbles on its own, it is
dimensionless, so it compares two players without either one's general precision
setting the scale. Values above 1 mean the inputs disagree more than they each
wobble; near 0 means they move as one.

**Precision cost of alternating.** Tap scatter on `1/4`-and-denser notes divided
by scatter on `1/2`-and-slower notes, measured within the same replays. It is a
within-player contrast, so it carries no overall skill difference. For a player
who finds streams disproportionately hard it is well above 1; for a player whose
accuracy is unchanged by density it is near 1.

## Commands

### `report.py` — make a report

```
python report.py REPLAYS [options]

  --out DIR             output folder (default: report)
  --maps DIR            where resolved .osu files are cached (default: <out>/maps)
  --songs DIR           osu! Songs folder to resolve beatmaps locally (repeatable)
  --meta-cache FILE     beatmap metadata cache (default: beatmap-metadata.json)
  --offline             never query the mirrors
  --formats md,html,json   which outputs to write (default: all)
  --no-charts           skip the PNG charts
  --ceil-300 N         300-rate that counts as holding a speed (default: 90)
  --ceil-min-taps N    minimum taps in a speed bucket to set the ceiling (default: 100)
  --drop-pp N          points below slow-note accuracy that count as a drop (default: 5)
  --bpm-bucket N       effective-BPM bucket width (default: 20)
  --max-replays N      stop early, for a quick look
  --quiet
```

The ceiling and drop thresholds are yours to set; the report always states the
values it used, so two reports are only compared like for like when they agree.

### `inspect-replays.py` — raw numbers, no report

```
python inspect-replays.py REPLAYS [--maps DIR] [--json out.json] [--playstyle-only]
```

Prints the aggregate tables and can dump everything to JSON. Useful for checking
that a replay set resolved correctly before sharing anything.

### `tools/` — the research scripts

The exploratory scripts used to develop the metrics above — significance testing,
per-spacing curves, hit-error distribution shape, sample selection. They are kept
because they document how the definitions were arrived at, and because they are
still useful for pooling several people's reports. They are not part of the
supported interface and some expect particular file layouts.

## Sharing results

See `docs/posting-results.md`. Short version: post `report.md` (with the
`charts/` folder) or `report.html`, plus `summary.json`. The summary is written
with a fixed key set so results from different players can be collected and
compared, and it records the thresholds the report was generated with.

## Layout

```
report.py             make a report from a folder of replays
inspect-replays.py    dump the raw numbers, no report
replaymetrics.py      the metric definitions: parsing, calibration, judging, labelling
render.py             markdown, self-contained HTML and charts
metadata.py           beatmap metadata (star rating, IDs for links), cached
analyze.py            judgement model, clock calibration, hit windows
osr_parse.py          .osr replay reader, no dependencies
osu_beatmap.py        .osu beatmap reader, no dependencies
fetch_map.py          beatmap lookup and download from public mirrors
batch.py              folder runner used by the research scripts
docs/                 method, metric definitions, playstyles, limitations, sharing
tools/                exploratory research scripts
examples/             an example report
```

## What it cannot tell you

A replay records when each input fired and where the cursor was. It does not
record which physical device fired, so a replay can say *that* two inputs were
used, not whether either is a pen. It cannot see your monitor, your audio latency
or your grip. A replay is also the game's own recording of a judgement already
made, so it can measure the timing of taps that were judged, not the taps the game
threw away.

`docs/limitations.md` covers this properly, and `docs/method.md` describes the
checks that let you decide how far to trust a given report.

## Notes

* Star ratings come from the public mirrors as they are **today**; a replay does
  not carry one, and a beatmap that has been edited since the play will be rated
  as it is now, or not resolve at all.
* Replays are read, never written. The tool never touches your game install.
* Beatmap lookups go to public mirrors only when a beatmap is not already cached.
  `--offline` prevents that entirely.
