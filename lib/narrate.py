"""Put your voice over a cut, and get the game out of its way.

Silent gameplay reads as filler — to people, and to YouTube's reused-content policy, which
is the one that decides whether a channel can ever be monetised. Your voice over your own
footage is the thing that makes it yours. This does the mixing part properly:

  - the voice is levelled and gently compressed, so a quiet line and a loud one sit together
  - the game audio ducks underneath it automatically whenever you are talking, and comes
    back up in the gaps, instead of being flattened to a constant murmur
  - the whole mix is normalised to the loudness every platform targets anyway

  cutaway narrate cut.mp4 voice.m4a -o episode.mp4
  cutaway narrate cut.mp4 voice.m4a -o episode.mp4 --game 0.25 --delay 0.4
"""
from __future__ import annotations

import pathlib
import subprocess
import sys


def mix(video: pathlib.Path, voice: pathlib.Path, out: pathlib.Path,
        game: float = 0.35, delay: float = 0.0, duck: float = 8.0) -> pathlib.Path:
    if not video.exists():
        sys.exit(f"no such video: {video}")
    if not voice.exists():
        sys.exit(f"no such recording: {voice}")
    out.parent.mkdir(parents=True, exist_ok=True)

    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)], capture_output=True, text=True).stdout.strip() or 0)

    pre = f"adelay={int(delay * 1000)}|{int(delay * 1000)}," if delay > 0 else ""
    # Padded to the full length on purpose: a sidechain ends when its shortest input does,
    # so an unpadded voice track cuts the game audio off the moment you stop talking.
    # not [v] — ffmpeg reads a bare "v" as the video stream specifier and refuses the graph
    voice_chain = (f"[1:a]{pre}highpass=f=85,afftdn=nf=-22,"
                   f"acompressor=threshold=-18dB:ratio=3:attack=12:release=180,"
                   f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur={dur:.3f}[vo]")

    if has_audio:
        chain = (
            f"{voice_chain};"
            # the voice is needed twice over — once to duck the game, once in the mix itself
            f"[vo]asplit=2[vo1][vo2];"
            f"[0:a]volume={game}[g];"
            # the game follows the voice: pulled down while you talk, released in the gaps
            f"[g][vo1]sidechaincompress=threshold=0.03:ratio={duck}:attack=20:release=400[gd];"
            f"[gd][vo2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        chain = f"{voice_chain};[vo]loudnorm=I=-14:TP=-1.5:LRA=11[a]"

    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-i", str(voice),
         "-filter_complex", chain, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
        check=True)
    print(f"{out}  {out.stat().st_size / 1e6:.1f} MB"
          + ("" if has_audio else "  (the cut had no audio of its own — voice only)"))
    return out


def script(text: str, seconds: float, wpm: int = 150) -> None:
    """Trim a piece of writing to what actually fits, and say what it will sound like."""
    words = text.split()
    fits = int(seconds * wpm / 60)
    spoken = " ".join(words[:fits])
    print(f"{seconds:.0f}s at {wpm} words a minute is about {fits} words. "
          f"You gave me {len(words)}.\n")
    print(spoken + ("" if len(words) <= fits else " …"))
    if len(words) > fits:
        print(f"\n({len(words) - fits} words over — cut the back half of the last sentence, "
              f"not the middle, or the ending lands flat.)")
