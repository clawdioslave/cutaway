"""Last night's episode, in one command.

  cutaway tonight                       # cut yesterday, shelf it, done
  cutaway tonight --date 2026-10-05     # a different night
  cutaway tonight --title "Chapter XI — The Mines" --story "a | b | c"
  cutaway tonight --publish             # upload straight away instead of shelving

Everything else in Cutaway is a part. This is the whole: find the recordings, cut them on
what actually happened, write the title and description, and put the result on the shelf
so the daily release lets it out. Run it from a timer and you have a channel that keeps
itself fed. Run it by hand and it is still one line.
"""
from __future__ import annotations

import datetime
import pathlib
import sys
import types

import config
import cut
import shelf


def run(a) -> int:
    cfg = config.load()
    day = a.date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    slug = a.slug or day
    out = pathlib.Path(cfg["out_dir"]).expanduser() / f"{slug}.mp4"
    title = a.title or day
    desc = a.desc
    if not desc:
        tpl = pathlib.Path(config.DIR / "description.txt")
        desc = tpl.read_text() if tpl.exists() else ""

    # cut.build reads an argparse-style namespace; build one with sensible defaults
    args = types.SimpleNamespace(
        out=str(out), night=day, clip=None, cuts=False, stills="", seconds=a.seconds,
        cut_len=a.cut_len, start=0.0, auto=False, hook="", sub="", tag=None,
        story=a.story, story_y=0.155, crop="", cam="", mute=False, no_moments=False,
        cold_open=not a.no_cold_open,
    )
    print(f"cutting {day} → {out.name}")
    cut.build(cfg, args)

    if a.publish:
        import youtube
        youtube.upload(out, title, desc, [t.strip() for t in a.tags.split(",") if t.strip()],
                       a.private, category=cfg.get("youtube_category", "20"))
    else:
        shelf.add(out, title, desc, a.tags)
    return 0
