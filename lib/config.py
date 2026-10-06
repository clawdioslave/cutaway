"""Where Cutaway keeps its settings, and the small amount of state it needs.

Everything lives in ~/.config/cutaway. Nothing is written anywhere else unless you
point it there yourself.
"""
from __future__ import annotations

import json
import os
import pathlib

HOME = pathlib.Path.home()
DIR = pathlib.Path(os.environ.get("CUTAWAY_HOME", HOME / ".config" / "cutaway"))
CONFIG = DIR / "config.json"

DEFAULTS = {
    # where your recorder drops files, and how to recognise one night's worth
    "clips_dir": "~/Movies",
    "clip_glob": "*.mp4",
    # {date} is replaced with the YYYY-MM-DD you ask for. OBS replay buffers are
    # named like Replay_2026-10-05_21-14-33.mp4, so the default matches those too.
    "night_glob": "*{date}*.mp4",
    # where finished verticals land
    "out_dir": "~/Movies/Cutaway",
    # the small credit in the bottom corner of every short. Empty to leave it off.
    "tag": "",
    # a .ttf you want the type set in. Empty picks a readable system face.
    "font": "",
    # crop of the recording to show, "x,y,w,h". Empty uses the whole frame.
    "crop": "",
    # crop of your webcam box inside the recording, "x,y,w,h", if your recorder
    # bakes one in and you want it carried into the short. Empty leaves it out.
    "cam": "",
    # the hairline under the gameplay strip, as r,g,b
    "rule": "214,174,88",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text()))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{CONFIG} is not valid JSON: {e}")
    return cfg


def save(cfg: dict) -> pathlib.Path:
    DIR.mkdir(parents=True, exist_ok=True)
    DIR.chmod(0o700)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    return CONFIG


def path(value: str) -> pathlib.Path:
    return pathlib.Path(value).expanduser()


def rgb(value: str, fallback=(214, 174, 88)) -> tuple:
    try:
        parts = tuple(int(x) for x in value.split(","))
        return parts if len(parts) == 3 else fallback
    except Exception:
        return fallback


def box(value: str):
    """'x,y,w,h' → a 4-tuple, or None when it is blank."""
    if not value:
        return None
    try:
        parts = tuple(int(x) for x in value.split(","))
    except ValueError:
        raise SystemExit(f"expected four numbers 'x,y,w,h', got {value!r}")
    if len(parts) != 4:
        raise SystemExit(f"expected four numbers 'x,y,w,h', got {value!r}")
    return parts
