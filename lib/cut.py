"""Turn a folder of 16:9 recordings into one 1080x1920 vertical video.

The whole idea is that a night of playing is mostly walking, and the three or four
seconds either side of each fight are the only part anyone wants to watch. So instead
of trimming thirty seconds out of the middle, this finds the busiest moments across
every recording from that night, cuts them to length, and hard-cuts them together in
the order they happened. Nothing is invented — every frame comes out of your footage.
"""
from __future__ import annotations

import glob as _glob
import json
import pathlib
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
]


def need(binary: str) -> None:
    if subprocess.run(["which", binary], capture_output=True).returncode != 0:
        sys.exit(f"{binary} is not installed. On a Mac:  brew install ffmpeg")


def font_path(preferred: str = "") -> str:
    if preferred and pathlib.Path(preferred).expanduser().exists():
        return str(pathlib.Path(preferred).expanduser())
    for cand in FONT_CANDIDATES:
        if pathlib.Path(cand).exists():
            return cand
    sys.exit("no usable font found — set \"font\" in your config to a .ttf you have")


def probe(path: pathlib.Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=width,height", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    s = (d.get("streams") or [{}])[0]
    return {"w": int(s.get("width") or 0), "h": int(s.get("height") or 0),
            "dur": float((d.get("format") or {}).get("duration") or 0)}


def brightness(path: pathlib.Path, t: float) -> float:
    """Mean luma of one frame. Loading screens, death screens and black desktops score near zero."""
    with tempfile.NamedTemporaryFile(suffix=".pgm", delete=False) as f:
        tmp = pathlib.Path(f.name)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(path),
                    "-frames:v", "1", "-vf", "scale=64:36,format=gray", str(tmp)], check=False)
    try:
        px = tmp.read_bytes().split(b"\n", 3)[-1]
        return sum(px) / max(1, len(px))
    except Exception:
        return 0.0
    finally:
        tmp.unlink(missing_ok=True)


def motion_profile(path: pathlib.Path, start: float, length: float) -> dict:
    """A scene-change score per second. Where the picture changes most is where the fight is."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
         "-vf", "scale=320:-2,fps=5,select='gte(scene\\,0)',metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True)
    prof, t = {}, None
    for line in (r.stdout or "").splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1)); continue
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and t is not None:
            prof[int(t)] = prof.get(int(t), 0.0) + float(m.group(1))
    return prof


def busiest(prof: dict, total: float, win: float) -> float:
    best, best_s = -1.0, 0.0
    for st in range(0, max(1, int(total - win))):
        v = sum(prof.get(k, 0.0) for k in range(st, st + int(win)))
        if v > best:
            best, best_s = v, float(st)
    return best_s


def load_moments(cfg: dict) -> dict:
    """The log of what triggered each recording, keyed by clip path.

    A replay buffer is saved *after* the thing happens, so the moment is near the end of
    the file, not in the middle. Scoring motion across the whole recording reliably finds
    the run-up instead: the camera swings hardest while you are travelling, and a fight is
    often a fairly still camera with effects over it. This is the ground truth that fixes it.
    """
    p = cfg.get("moments_log", "")
    if not p:
        return {}
    f = pathlib.Path(p).expanduser()
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("clip") and d.get("t"):
            out[str(d["clip"])] = float(d["t"])
    return out


def load_events(cfg: dict) -> list:
    """A weighted log of things that happened, as [(unix_time, label, weight), ...].

    Richer than the moments log: a moment only says "something happened here", while an
    event says what and how much it was worth. That matters because the things a recorder
    saves are not equally interesting — arriving in a new zone and a two-minute fight with
    an elite both trip the shutter, and only one of them is worth thirty seconds of
    somebody's attention. Weight is what decides which cuts make the episode.
    """
    p = cfg.get("events_log", "")
    if not p:
        return []
    f = pathlib.Path(p).expanduser()
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("t"):
            out.append((float(d["t"]), str(d.get("label") or ""), float(d.get("weight") or 1.0)))
    return sorted(out)


def clip_span(path: pathlib.Path, dur: float) -> tuple:
    """The wall-clock window a recording covers. A replay buffer ends when it is written."""
    end = path.stat().st_mtime
    return end - dur, end


def event_in_clip(path: pathlib.Path, events: list, dur: float):
    """The best logged event inside this recording: (seconds_in, label, weight)."""
    lo, hi = clip_span(path, dur)
    best = None
    for t, label, weight in events:
        if lo <= t <= hi:
            if best is None or weight > best[2]:
                best = (t - lo, label, weight)
    return best


def event_time(path: pathlib.Path, moments: dict, dur: float):
    """Seconds from the start of the recording to the thing that caused it to be saved."""
    t = moments.get(str(path))
    if not t:
        return None
    ev = t - (path.stat().st_mtime - dur)
    return ev if 0.0 <= ev <= dur else None


def anchored_cut(path: pathlib.Path, seg: float, ev: float, dur: float, slack: float = 2.5):
    """The best `seg` seconds around a known moment.

    The moment itself is always inside the cut — that is the entire point, and motion alone
    will not keep it there. Travel scores higher than fighting, so an unconstrained search
    slides into the run-up every time. The window here only spans starts that leave the
    event on screen with a beat either side; motion picks the liveliest of those.
    """
    lo = max(0.0, ev - seg + 0.3)                      # event no later than 0.3s before the end
    hi = min(max(0.0, dur - seg), max(0.0, ev - 0.5))  # and no sooner than 0.5s after the start
    if hi < lo:
        lo = hi = max(0.0, min(dur - seg, ev - seg / 2))
    prof = motion_profile(path, lo, (hi - lo) + seg)
    best, best_st = None, lo
    st = lo
    while st <= hi + 0.01:
        rel = st - lo
        score = sum(prof.get(k, 0.0) for k in range(int(rel), int(rel + seg) + 1))
        if best is None or score > best:
            best, best_st = score, st
        st += 0.5
    if brightness(path, best_st + seg / 2) < 20:
        return None
    return best_st, (best or 0.0)


def peaks(path: pathlib.Path, seg: float, keep: int) -> list:
    """The best `keep` moments in one recording: most motion, non-overlapping, bright enough to see."""
    info = probe(path)
    prof = motion_profile(path, 0, info["dur"])
    scored = []
    for st in range(0, max(1, int(info["dur"] - seg))):
        scored.append((sum(prof.get(k, 0.0) for k in range(st, st + int(seg) + 1)), float(st)))
    scored.sort(reverse=True)
    chosen: list = []
    for _score, st in scored:
        if len(chosen) >= keep:
            break
        if any(abs(st - c) < seg + 1 for c in chosen):
            continue
        if brightness(path, st + seg / 2) < 26:
            continue
        chosen.append(st)
    return sorted(chosen)


def assemble(clips: list, total: float, seg: float, tmp: pathlib.Path,
             moments: dict | None = None, events: list | None = None,
             labels: list | None = None) -> pathlib.Path:
    """Cut the night down to its moments and hard-cut them together, oldest first.

    With a moments log, each recording contributes the one cut around the thing that caused
    it to be saved, and the recordings with the most going on at that instant win the slots.
    Without one, this falls back to scoring motion across each whole recording.
    """
    want = max(2, int(round(total / seg)))
    moments = moments or {}
    labels = labels if labels is not None else []
    picks, anchored, weighted = [], 0, 0

    scored = []
    for c in clips:
        dur = probe(c)["dur"]
        hit = event_in_clip(c, events or [], dur)            # a weighted event beats a bare moment
        if hit:
            ev, label, weight = hit
        else:
            ev, label, weight = event_time(c, moments, dur), "", None
        if ev is None:
            continue
        got = anchored_cut(c, seg, ev, dur)
        if not got:
            continue
        # With weights, the log decides what is worth showing. Without them, all we can ask
        # is which moment had the most going on, which quietly favours travel over fighting.
        rank = weight if weight is not None else got[1]
        scored.append((weight is not None, rank, c, got[0], label))
    if scored:
        scored.sort(reverse=True, key=lambda r: (r[0], r[1]))
        chosen = scored[:want]
        chosen.sort(key=lambda r: r[2].name)                 # then back into the order they happened
        picks = [(c, st) for _w, _r, c, st, _l in chosen]
        labels.extend(l for _w, _r, _c, _s, l in chosen)
        anchored = len(picks)
        weighted = sum(1 for r in chosen if r[0])

    if len(picks) < want:                                    # top up from recordings with no moment logged
        used = {c for c, _ in picks}
        spare = [c for c in clips if c not in used]
        per_clip = max(1, (want - len(picks)) // max(1, len(spare)) + 1) if spare else 0
        extra = []
        for c in spare:
            for st in peaks(c, seg, per_clip):
                extra.append((c, st))
        extra.sort(key=lambda x: (x[0].name, x[1]))
        picks += extra[: want - len(picks)]
        picks.sort(key=lambda x: (x[0].name, x[1]))

    if not picks:
        sys.exit("no usable moments found — the recordings may be too short or too dark")
    if anchored:
        detail = f", {weighted} of them to a weighted event" if weighted else ""
        print(f"  {anchored} of {len(picks)} cuts anchored to a logged moment{detail}")
    parts = []
    for i, (clip, st) in enumerate(picks):
        out = tmp / f"cut{i:02d}.mp4"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{st:.2f}", "-t", f"{seg:.2f}", "-i", str(clip),
             "-vf", "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
             "-af", f"afade=t=in:st=0:d=0.06,afade=t=out:st={max(0.0, seg - 0.1):.2f}:d=0.1",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", str(out)], check=True)
        parts.append(out)
    lst = tmp / "cuts.txt"
    lst.write_text("".join(f"file '{q}'\n" for q in parts))
    base = tmp / "assembled.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(base)], check=True)
    print(f"  assembled {len(parts)} cuts of {seg:.1f}s from {len(set(c for c, _ in picks))} recording(s)")
    return base


def night_clips(cfg: dict, day: str, most: int = 14) -> list:
    """Every recording from one date. Comma-separate prefixes when a night spans two sessions."""
    d = pathlib.Path(cfg["clips_dir"]).expanduser()
    if not d.is_dir():
        sys.exit(f"clips_dir {d} does not exist — run: cutaway setup")
    found: list = []
    for part in [x.strip() for x in day.split(",") if x.strip()]:
        found += list(d.glob(cfg["night_glob"].replace("{date}", part)))
    if not found:
        sys.exit(f"no recordings in {d} matching {cfg['night_glob'].replace('{date}', day)}")
    return sorted(set(found), key=lambda q: -q.stat().st_size)[:most]


def stills_clip(pattern: str, seconds: float, tmp: pathlib.Path) -> pathlib.Path:
    """A night with no recording still has screenshots. Pan slowly over them instead."""
    shots = sorted(_glob.glob(str(pathlib.Path(pattern).expanduser())))
    if not shots:
        sys.exit(f"no pictures matched {pattern}")
    per = max(2.4, seconds / len(shots))
    parts = []
    for i, shot in enumerate(shots):
        seg = tmp / f"seg{i:02d}.mp4"
        frames = int(per * 30)
        zexpr = "min(zoom+0.0012,1.25)" if i % 2 == 0 else "if(lte(zoom,1.0),1.25,max(1.001,zoom-0.0012))"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", shot, "-t", f"{per:.2f}",
             "-vf", f"scale=3840:-2,zoompan=z='{zexpr}':d={frames}:x='iw/2-(iw/zoom/2)':"
                    f"y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(seg)], check=True)
        parts.append(seg)
    lst = tmp / "parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    base = tmp / "stills.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(base)], check=True)
    return base


def wrap(d, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > maxw and cur:
            lines.append(cur); cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def parse_story(spec: str, dur: float) -> list:
    """'line | line | line' spreads evenly; 'a-b:line' pins a beat to seconds a..b; '.' is silence."""
    raw = [x.strip() for x in spec.split("|") if x.strip()]
    timed, untimed = [], []
    for i, part in enumerate(raw):
        head = part.split(":", 1)[0]
        if ":" in part and "-" in head:
            span, text = part.split(":", 1)
            a, b = span.split("-")
            timed.append((float(a), float(b), text.strip(), i))
        else:
            untimed.append((part, i))
    if untimed:
        taken = sum(b - a for a, b, _, _ in timed)
        each = max(1.2, (dur - taken) / len(untimed))
        slots = [(a, b) for a, b, _, _ in timed]
        t = 0.0
        for text, i in untimed:
            clash = [b for a, b in slots if t < b and t + each > a]
            if clash:
                t = max(clash)
            timed.append((t, min(dur, t + each), text, i))
            t += each
    timed.sort(key=lambda x: x[3])
    return [(a, b, txt) for a, b, txt, _ in timed if txt != "."]


def make_beat(path, text, fp, y_frac=0.155):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(fp, 62)
    lines = wrap(d, text, f, W - 130)
    block_h = len(lines) * 76
    y = round(H * y_frac) - block_h // 2
    pad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(pad).rectangle([0, y - 44, W, y + block_h + 34], fill=(6, 7, 14, 155))
    im = Image.alpha_composite(im, pad.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(im)
    for line in lines:
        x = (W - d.textlength(line, font=f)) / 2
        for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
            d.text((x + ox, y + oy), line, font=f, fill=(0, 0, 0, 225))
        d.text((x, y), line, font=f, fill=(255, 252, 244, 255))
        y += 76
    im.save(path)


def make_overlay(path, hook, sub, tag, game_y, game_h, fp, rule):
    """Type is drawn here with Pillow, not by ffmpeg — plenty of ffmpeg builds ship without drawtext."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rectangle([0, 0, W, round(H * 0.26)], fill=(6, 7, 14, 165))
    bd.rectangle([0, H - 320, W, H], fill=(6, 7, 14, 150))
    im = Image.alpha_composite(im, band.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(im)

    y = round(H * 0.075)
    if hook:
        f = ImageFont.truetype(fp, 74)
        for line in wrap(d, hook, f, W - 110):
            x = (W - d.textlength(line, font=f)) / 2
            for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
                d.text((x + ox, y + oy), line, font=f, fill=(0, 0, 0, 220))
            d.text((x, y), line, font=f, fill=(255, 255, 255, 255))
            y += 86
    if sub:
        f = ImageFont.truetype(fp, 46)
        y += 10
        for line in wrap(d, sub, f, W - 150):
            x = (W - d.textlength(line, font=f)) / 2
            for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                d.text((x + ox, y + oy), line, font=f, fill=(0, 0, 0, 210))
            d.text((x, y), line, font=f, fill=(232, 217, 168, 255))
            y += 56
    gy = game_y + game_h + 18
    d.line([(W * 0.18, gy), (W * 0.82, gy)], fill=(*rule, 150), width=2)
    if tag:
        f = ImageFont.truetype(fp, 38)
        x = (W - d.textlength(tag, font=f)) / 2
        for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            d.text((x + ox, H - 104 + oy), tag, font=f, fill=(0, 0, 0, 200))
        d.text((x, H - 104), tag, font=f, fill=(255, 255, 255, 200))
    im.save(path)


def pick(cfg: dict, folder: str, scan: int, top: int) -> None:
    """Rank recordings by size (more motion makes a bigger file) and drop the dark ones."""
    d = pathlib.Path(folder or cfg["clips_dir"]).expanduser()
    if not d.is_dir():
        sys.exit(f"{d} does not exist")
    files = sorted(d.glob(cfg["clip_glob"]), key=lambda p: p.stat().st_mtime, reverse=True)[:scan]
    rows = []
    for p in files:
        info = probe(p)
        if info["dur"] < 8:
            continue
        lum = brightness(p, min(info["dur"] - 2, info["dur"] * 0.6))
        if lum <= 28:
            continue
        rows.append((p.stat().st_size / 1e6, lum, info["dur"], p))
    rows.sort(key=lambda r: -r[0])
    print(f"{len(rows)} usable of {len(files)} scanned, busiest first:\n")
    for mb, lum, dur, p in rows[:top]:
        print(f"  {mb:6.1f} MB  {dur:5.1f}s  luma {lum:3.0f}  {p.name}")
    if rows:
        day = re.search(r"(\d{4}-\d{2}-\d{2})", rows[0][3].name)
        hint = f"--night {day.group(1)}" if day else f'"{rows[0][3]}" --cuts'
        print(f"\n  cutaway build {hint} -o short.mp4")


def build(cfg: dict, a) -> pathlib.Path:
    need("ffmpeg"); need("ffprobe")
    out = pathlib.Path(a.out).expanduser()
    if not out.is_absolute() and not str(out).startswith("."):
        out = pathlib.Path(cfg["out_dir"]).expanduser() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    fp = font_path(cfg.get("font", ""))
    rule = tuple(int(x) for x in cfg.get("rule", "214,174,88").split(","))

    holder = tempfile.TemporaryDirectory()
    stage = pathlib.Path(holder.name)
    cut_labels: list = []
    auto, start, mute = a.auto, a.start, a.mute

    if a.night or a.cuts:
        clips = night_clips(cfg, a.night) if a.night else [pathlib.Path(a.clip).expanduser()]
        off = getattr(a, "no_moments", False)
        moments = {} if off else load_moments(cfg)
        events = [] if off else load_events(cfg)
        src = assemble(clips, a.seconds, a.cut_len, stage, moments, events, cut_labels)
        auto, start = False, 0.0
    elif a.stills:
        src = stills_clip(a.stills, a.seconds, stage)
        auto, start, mute = False, 0.0, True
    else:
        src = pathlib.Path(a.clip).expanduser()
        if not src.exists():
            sys.exit(f"no such recording: {src}")

    info = probe(src)
    if not info["w"]:
        sys.exit(f"cannot read {src}")
    if auto:
        start = busiest(motion_profile(src, 0, info["dur"]), info["dur"], a.seconds)
        print(f"  busiest {a.seconds:.0f}s starts at {start:.0f}s")
    dur = min(a.seconds, max(1.0, info["dur"] - start))

    crop = _box(a.crop or cfg.get("crop", "")) or (0, 0, info["w"], info["h"])
    cam = _box(a.cam or cfg.get("cam", ""))
    game_h = round(W * crop[3] / crop[2])
    game_y = round(H * 0.245)

    with tempfile.TemporaryDirectory() as td:
        ov = pathlib.Path(td) / "overlay.png"
        tag = a.tag if a.tag is not None else cfg.get("tag", "")
        make_overlay(ov, a.hook, a.sub, tag, game_y, game_h, fp, rule)
        extra = [str(ov)]
        chain = [
            f"[0:v]scale=-2:{H}:flags=bilinear,crop={W}:{H}:(iw-{W})/2:0,boxblur=28:2,"
            f"eq=brightness=-0.16:saturation=0.8[bg]",
            f"[0:v]crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},scale={W}:{game_h}:flags=lanczos[game]",
            f"[bg][game]overlay=0:{game_y}[v1]",
        ]
        last = "v1"
        if cam:
            cam_w = 520
            cam_h = round(cam_w * cam[3] / cam[2])
            chain.append(f"[0:v]crop={cam[2]}:{cam[3]}:{cam[0]}:{cam[1]},scale={cam_w}:{cam_h}:flags=lanczos[cam]")
            chain.append(f"[{last}][cam]overlay=(W-{cam_w})/2:{game_y + game_h + 56}[v2]")
            last = "v2"
        chain.append(f"[{last}][1:v]overlay=0:0[v3]")
        last = "v3"
        for n, (t0, t1, text) in enumerate(parse_story(a.story, dur) if a.story else []):
            bp = pathlib.Path(td) / f"beat{n}.png"
            make_beat(bp, text, fp, a.story_y)
            extra.append(str(bp))
            chain.append(f"[{last}][{2 + n}:v]overlay=0:0:enable='between(t,{t0:.2f},{t1:.2f})'[b{n}]")
            last = f"b{n}"
        chain.append(f"[{last}]null[vout]")

        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(dur), "-i", str(src)]
        for e in extra:
            cmd += ["-i", e]
        cmd += ["-filter_complex", ";".join(chain), "-map", "[vout]"]
        if mute:
            cmd += ["-an"]
        else:
            cmd += ["-map", "0:a?", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k"]
        cmd += ["-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    holder.cleanup()
    print(f"{out}  {W}x{H}  {dur:.0f}s  {out.stat().st_size / 1e6:.1f} MB")
    return out


def _box(value: str):
    if not value:
        return None
    parts = tuple(int(x) for x in value.split(","))
    if len(parts) != 4:
        sys.exit(f"expected 'x,y,w,h', got {value!r}")
    return parts
