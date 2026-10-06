"""A shelf of finished videos waiting their turn, and the thing that lets one out a day.

Publishing ten episodes in an afternoon is not a catalogue, it is a dump. Nothing can
learn from it, nobody can follow it, and the tenth buries the first. The shelf exists so
building and publishing come apart: cut whenever you have footage, release on a schedule.

Order is the order you added them. `release` takes the oldest one still waiting.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

HOME = pathlib.Path(os.environ.get("CUTAWAY_HOME", pathlib.Path.home() / ".config" / "cutaway"))
SHELF = HOME / "shelf.json"


def _read() -> list:
    if not SHELF.exists():
        return []
    try:
        return json.loads(SHELF.read_text())
    except json.JSONDecodeError:
        sys.exit(f"{SHELF} is not valid JSON — fix or delete it")


def _write(items: list) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    SHELF.write_text(json.dumps(items, indent=2) + "\n")


def add(path: pathlib.Path, title: str, desc: str, tags: str = "") -> None:
    if not path.exists():
        sys.exit(f"no such file: {path}")
    if not title:
        sys.exit("--title is required so you know what you are releasing later")
    items = _read()
    if any(i["file"] == str(path.resolve()) and not i.get("published") for i in items):
        sys.exit(f"{path.name} is already on the shelf")
    items.append({
        "file": str(path.resolve()),
        "title": title,
        "desc": desc,
        "tags": tags,
        "added": datetime.datetime.now().isoformat(timespec="seconds"),
        "published": None,
        "url": None,
    })
    _write(items)
    waiting = sum(1 for i in items if not i.get("published"))
    print(f"shelved: {title}")
    print(f"{waiting} waiting — {waiting} day(s) of releases at one a day")


def show() -> None:
    items = _read()
    if not items:
        print("the shelf is empty.  cutaway shelf add <file> --title \"...\"")
        return
    waiting = [i for i in items if not i.get("published")]
    done = [i for i in items if i.get("published")]
    if waiting:
        print(f"waiting ({len(waiting)}):")
        for n, i in enumerate(waiting, 1):
            miss = "" if pathlib.Path(i["file"]).exists() else "   ⚠ file is gone"
            print(f"  {n}.  {i['title']}{miss}")
    if done:
        print(f"\nreleased ({len(done)}):")
        for i in done[-5:]:
            print(f"  {i['published'][:10]}  {i['title']}  {i.get('url') or ''}")


def release(private: bool = False, dry_run: bool = False) -> int:
    """Publish the oldest video still waiting. Safe to run every day on an empty shelf."""
    items = _read()
    pending = [i for i in items if not i.get("published")]
    if not pending:
        print("nothing waiting — the shelf is empty, so nothing was published")
        return 0
    item = pending[0]
    path = pathlib.Path(item["file"])
    if not path.exists():
        sys.exit(f"{path} is gone — fix the shelf entry or remove it")
    print(f"releasing: {item['title']}")
    if dry_run:
        print(f"dry run — would upload {path}")
        return 0

    import youtube
    vid = youtube.upload(path, item["title"], item["desc"],
                         [t.strip() for t in (item.get("tags") or "").split(",") if t.strip()],
                         private)
    item["published"] = datetime.datetime.now().isoformat(timespec="seconds")
    item["url"] = f"https://youtube.com/watch?v={vid}"
    _write(items)
    left = sum(1 for i in items if not i.get("published"))
    print(f"{left} still waiting")
    return 0


def drop(n: int) -> None:
    items = _read()
    pending = [i for i in items if not i.get("published")]
    if not 1 <= n <= len(pending):
        sys.exit(f"there is no waiting item {n} — run: cutaway shelf list")
    gone = pending[n - 1]
    items.remove(gone)
    _write(items)
    print(f"removed from the shelf (the file is untouched): {gone['title']}")
