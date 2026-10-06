# Cutaway

Turn a night of recordings into a vertical video worth watching, then put it out.

A night of playing is mostly walking. The three seconds either side of each fight are the
only part anyone wants to see. Cutaway reads every recording from one date, scores them
second by second for how much the picture is actually changing, picks the busiest moments
that aren't loading screens, and hard-cuts them together in the order they happened. Then
it stacks the result into a 1080×1920 frame, sets your words over it, and uploads it.

Nothing is generated. Every frame comes out of your own footage.

```
cutaway build --night 2026-10-05 -o ch11.mp4 --story "the long way home | then the vale | six harpies"
cutaway upload ch11.mp4 --title "Chapter XI" --desc "..."
cutaway post --text "new one up" --video ch11.mp4
```

Built for, and used nightly by, [The Chronicle of Cassidy](https://clawdioslave.github.io/the-chronicle/).

---

## What you need

- a Mac or Linux box with **ffmpeg** (`brew install ffmpeg`)
- **Python 3.9+** with pillow and requests (`python3 -m pip install --user pillow requests requests-oauthlib`)
- a folder of recordings — OBS's replay buffer is ideal, but any `.mp4` works
- optionally: an X developer app, and a Google Cloud project for YouTube

Install:

```bash
git clone https://github.com/clawdioslave/cutaway.git ~/cutaway
~/cutaway/cutaway setup
~/cutaway/cutaway doctor
```

Put `~/cutaway` on your `PATH` if you want to type `cutaway` instead of the full path.

---

## Setup

`cutaway setup` asks six questions and writes `~/.config/cutaway/config.json`:

| key | what it is |
| --- | --- |
| `clips_dir` | where your recorder drops files |
| `night_glob` | how to recognise one night, with `{date}` standing in for `2026-10-05`. OBS replay buffers match the default. |
| `out_dir` | where finished verticals land |
| `tag` | the small credit in the bottom corner. Blank leaves it off. |
| `crop` | `x,y,w,h` of the recording to show. Blank uses the whole frame. |
| `cam` | `x,y,w,h` of a webcam box baked into the recording, if you want it carried through. Blank leaves it out. |

The two crops are the only fiddly part, and only if your recording has furniture you don't
want on camera — a title bar, a quest log, your second monitor. Grab a frame, open it in
Preview, and read the rectangle off the selection:

```bash
ffmpeg -ss 30 -i "your-recording.mp4" -frames:v 1 frame.png && open frame.png
```

Then `cutaway setup x` and `cutaway setup youtube` if you want it to post for you. Both are
optional; `cutaway build` works on its own.

---

## Commands

### `cutaway pick`

Ranks your recent recordings by how much is going on in them and drops the dark ones, so
you know which night is worth cutting before you spend the minutes.

```
 146.3 MB   58.1s  luma  42  Replay_2026-10-05_17-16-43.mp4
  95.5 MB   58.4s  luma  98  Replay_2026-10-05_20-12-40.mp4
```

### `cutaway build`

The part that matters.

```bash
cutaway build --night 2026-10-05 -o tonight.mp4
```

- `--night YYYY-MM-DD` — every recording from that date, cut down to its moments. Comma-separate
  dates (`--night 2026-10-05,2026-10-06`) when one session spans midnight.
- `--seconds 28` — total length. Shorts are capped at 3 minutes, but under 30 seconds holds.
- `--cut-len 3.2` — how long each cut runs. Shorter is punchier and gets jarring below ~2s.
- `--cuts <file>` — the same treatment applied to one recording instead of a whole night.
- `--auto <file>` — the old behaviour: one continuous stretch, the busiest one. Use it when the
  thing you're showing needs to play out in real time.
- `--stills '<glob>'` — no recording from that night? Pan slowly over screenshots instead.

Words on top:

- `--hook "..."` and `--sub "..."` — the title block at the top, held for the whole video.
- `--story "a | b | 12-18:c | ."` — timed beats. Bars split them, they spread evenly across the
  length, `12-18:` pins one to those seconds, and a lone `.` is a deliberate silence.
- `--story-y 0.155` — where the beats sit vertically, 0 to 1.

Beats are what turns a montage into something people finish. Three or four short lines that
describe what's happening, in your own voice. If you can't write one, that's usually a sign
the night wasn't a story and the cut will feel like filler.

### Cutting on real events, not motion

This is the part that matters most, and it is worth explaining why.

Scoring motion across a recording sounds right and is quietly wrong. Scene-change score
peaks when the **camera** moves — running through trees, swinging the view around — and a
fight is often a fairly still camera with effects over it. Measured against a log of what
actually happened, a pure motion picker missed the real moment by **12 to 37 seconds on
four clips out of four**. It reliably found the walk *toward* the thing.

If your recorder saves a replay buffer when something happens, the fix is free, because the
moment is already recorded — near the *end* of each file, since the buffer is written after
the fact. Point Cutaway at a log of it:

```jsonc
// moments_log — one object per line
{"t": 1791247947.89, "clip": "/Users/you/Movies/Replay_2026-10-05_20-52-34.mp4"}
```

Each recording then contributes one cut, guaranteed to contain the moment with a beat
either side. Motion only chooses *where* inside that window.

Better still, if you can say how much each thing was worth:

```jsonc
// events_log — matched to recordings by time, no clip path needed
{"t": 1791247947.89, "label": "Dark Strand Fanatic", "weight": 151.6}
```

Weight is what decides which moments make the episode. Without it, all Cutaway can ask is
which moment had the most going on — which quietly favours arriving somewhere over fighting
something, because travel moves the camera more. With it, the long fight wins the slot.

`--no-moments` turns all of this off and goes back to scoring motion, if you want to compare.

### `cutaway shelf`

Building and publishing should not be the same act. Ten episodes released in an afternoon
is a dump: nothing can learn from it, nobody can follow it, and the tenth buries the first.

```bash
cutaway shelf add tonight.mp4 --title "Chapter XI" --desc "$(cat desc.txt)"
cutaway shelf list
cutaway shelf release          # publishes the oldest one still waiting
```

Cut whenever you have footage; put `cutaway shelf release` behind a daily timer and the
backlog meters itself out. Run it on an empty shelf and it does nothing, quietly.

### `cutaway narrate`

```bash
cutaway script chapter.txt --seconds 28      # trim your writing to what actually fits
cutaway narrate cut.mp4 voice.m4a -o episode.mp4
```

Levels and compresses your voice, then ducks the game underneath it with a sidechain — down
while you talk, back up in the gaps — rather than flattening it to a constant murmur. The
whole mix lands at the loudness every platform targets anyway.

This is also the honest answer to YouTube's reused-content rule. Silent gameplay is the
thing that policy exists to catch; your voice over your own footage is what makes it yours.

### `cutaway upload`

```bash
cutaway upload tonight.mp4 --title "Chapter XI" --desc "$(cat desc.txt)" --tags wow,gameplay
```

Public by default — pass `--private` while you're testing. It sets "not made for kids" for you,
which YouTube requires and which you lose personalised ads without. Vertical and under three
minutes is all it takes to be treated as a Short; put `#Shorts` in the description anyway.

`cutaway upload --check` confirms which channel you're pointed at before you send anything.

### `cutaway post`

```bash
cutaway post --check
cutaway post --text "new one up" --video tonight.mp4
cutaway post --from-draft drafts/tonight.md
cutaway post --text "..." --dry-run
```

`--from-draft` reads a markdown file: the first fenced block is the text, and `clip:` or
`image:` lines point at the media. It's an easier thing to keep a queue of than a shell history.

X's free tier posts about 17 times a day, which is more than enough. It **cannot** reply to
other people's posts — that single call returns 403 until you're on a paid tier. Everything
else works.

---

## Doing it every night

The whole point is that this runs without you. A `launchd` job (or a cron line) that fires
after you normally log off:

```bash
#!/bin/zsh
DAY=$(date -v-1d +%F)            # last night
OUT=~/Movies/Cutaway/$DAY.mp4
cutaway build --night "$DAY" -o "$OUT" --seconds 28 || exit 0
cutaway upload "$OUT" --title "$DAY" --desc "$(cat ~/desc.txt)" --private
```

Upload `--private` from a script and promote it by hand once you've watched it. Automated
uploads you never look at are how a channel ends up with a hundred videos nobody finished.

---

## A word about what gets watched

Cutaway makes the cutting fast. It does not make the video good. The things that actually
moved the needle on the channel this came from:

- **The beats.** Raw gameplay with no commentary and no text reads as filler, to people and to
  YouTube's "reused content" policy both. Your words over your footage are the thing that makes
  it yours.
- **Serialisation.** "Chapter XI" gets the people who watched Chapter X. A pile of unconnected
  clips doesn't compound.
- **Cut harder than feels right.** The first version of this tool took one continuous 30-second
  window and the result was a beautifully chosen walk through a forest. Nobody watched it.
- **One a day beats five on Sunday.**

---

## Licence

MIT. Do whatever you want with it.

Made alongside [Masque](https://clawdioslave.com/masque/) and the rest of
[clawdioslave.com](https://clawdioslave.com).
