"""Read a script over a finished cut, in one command.

The mixing was never the hard part — sitting down to record was. So this does the whole
thing in one go: works out how many words fit in the video, puts the script on screen,
counts you in, records for exactly as long as the video runs, and mixes it underneath.
If you fluff it, run it again; nothing is written until the take is done.

  cutaway voice episode.mp4 --script chapter.txt -o narrated.mp4
  cutaway voice episode.mp4 --text "..." -o narrated.mp4 --device 1
  cutaway voice --devices          # which microphones this machine can see
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time

import narrate


def devices() -> list:
    """The audio inputs ffmpeg can see, as [(index, name), ...]."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation",
                        "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    out, seen = [], False
    for line in (r.stderr or "").splitlines():
        if "AVFoundation audio devices" in line:
            seen = True; continue
        if seen:
            m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
            if not m:
                break
            out.append((int(m.group(1)), m.group(2)))
    return out


def show_devices() -> None:
    d = devices()
    if not d:
        sys.exit("no audio inputs found — on a Mac this needs microphone permission "
                 "for your terminal in System Settings → Privacy & Security → Microphone")
    print("microphones:")
    for i, name in d:
        print(f"  --device {i}   {name}")
    print("\nPick the one you actually talk into; a headset beats a webcam every time.")


def pick(preferred) -> int:
    d = devices()
    if not d:
        sys.exit("no audio inputs found — run: cutaway voice --devices")
    if preferred is not None:
        if any(i == preferred for i, _ in d):
            return preferred
        sys.exit(f"no microphone {preferred} — run: cutaway voice --devices")
    # a headset is almost always the better of the two, and almost always not index 0
    for i, name in d:
        if re.search(r"head|quantum|airpod|usb|yeti|shure|rode|podcast", name, re.I):
            return i
    return d[0][0]


def duration(path: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def record(dev: int, seconds: float, out: pathlib.Path) -> pathlib.Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "avfoundation", "-i", f":{dev}",
         "-t", f"{seconds:.2f}", "-ac", "1", "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
         str(out)], stdin=subprocess.DEVNULL)
    start = time.time()
    while p.poll() is None:
        left = seconds - (time.time() - start)
        if left < 0:
            break
        print(f"\r  ● recording … {left:4.1f}s left ", end="", flush=True)
        time.sleep(0.1)
    p.wait()
    print("\r  ✓ take recorded" + " " * 24)
    if not out.exists() or out.stat().st_size < 2000:
        sys.exit("nothing was recorded — check microphone permission for your terminal in "
                 "System Settings → Privacy & Security → Microphone")
    return out


def level(path: pathlib.Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    return float(m.group(1)) if m else 0.0


def run(video: pathlib.Path, text: str, out: pathlib.Path, dev, game: float,
        wpm: int, keep: str = "") -> None:
    if not video.exists():
        sys.exit(f"no such video: {video}")
    secs = duration(video)
    if secs <= 0:
        sys.exit(f"cannot read {video}")
    d = pick(dev)
    name = dict(devices()).get(d, "?")

    words = text.split()
    fits = int(secs * wpm / 60)
    if len(words) > fits:
        print(f"the script is {len(words)} words; about {fits} fit in {secs:.0f}s at {wpm} wpm.")
        print(f"trimming the last {len(words) - fits}. Shorten it yourself if the ending matters.\n")
        words = words[:fits]
    script = " ".join(words)

    print("─" * 64)
    for line in _wrap(script, 60):
        print("  " + line)
    print("─" * 64)
    print(f"  {secs:.0f}s · {len(words)} words · mic: {name}\n")

    for n in (3, 2, 1):
        print(f"\r  starting in {n} … ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 24, end="\r")

    tmp = pathlib.Path(keep).expanduser() if keep else out.with_suffix(".voice.m4a")
    record(d, secs + 1.0, tmp)

    db = level(tmp)
    if db < -45:
        print(f"  ⚠ that take is very quiet ({db:.0f} dB). Wrong microphone, or muted?")
    elif db > -8:
        print(f"  ⚠ that take is hot ({db:.0f} dB) and may be clipping. Back off the mic.")

    narrate.mix(video, tmp, out, game=game)
    if not keep:
        tmp.unlink(missing_ok=True)
    print("\nIf the take was bad, run the same command again — nothing else was changed.")


def _wrap(s: str, w: int) -> list:
    out, cur = [], ""
    for word in s.split():
        if len(cur) + len(word) + 1 > w and cur:
            out.append(cur); cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out
