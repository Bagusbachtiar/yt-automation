#!/usr/bin/env python3
"""
YouTube Shorts assembly prototype — ffmpeg only, no cloud deps.

Expected layout before running:
  script.json        — JSON from Ollama (see script_prompt_template.txt)
  images/1.jpg       — one image per line ID (jpg/jpeg/png/webp accepted)
  images/2.jpg
  audio/1.wav        — OPTIONAL: per-line TTS audio (wav/mp3/m4a/ogg)
                       if absent, line gets DEFAULT_LINE_DURATION seconds of silence

Output:
  output.mp4         — 1080x1920 portrait, H.264, AAC, captions, Ken Burns zoom, crossfades

Run:
  python assemble.py
"""

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
RES_W, RES_H = 1080, 1920          # portrait 9:16 for Shorts
FPS = 30
CROSSFADE_SECS = 0.5               # video crossfade between segments
AUDIO_XFADE_SECS = 0.05           # audio crossfade — near-instant to avoid volume ramp
DEFAULT_LINE_SECS = 5.0            # seconds per line when no audio provided
FONT_SIZE = 56
CAPTION_WRAP_CHARS = 30            # wrap caption text at this width (~2-3 lines max)
ZOOM_SPEED = 0.0015                # Ken Burns zoom increment per frame
MAX_ZOOM = 1.5

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_JSON = Path("script.json")
IMAGES_DIR  = Path("images")
AUDIO_DIR   = Path("audio")
OUTPUT      = Path("output.mp4")
TMP_SEGS    = Path("tmp_segments")
TMP_TXT     = Path("tmp_text")

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp"]
AUDIO_EXTS = [".wav", ".mp3", ".m4a", ".ogg"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def check_deps():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"[ERROR] '{tool}' not found in PATH — install ffmpeg first.")


def get_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def find_file(directory: Path, stem: str, exts) -> Optional[Path]:
    for ext in exts:
        p = directory / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def setup_font() -> str:
    """Copy Arial Bold to project dir so ffmpeg fontfile= needs no colon in path."""
    local = Path("arialbd.ttf")
    if not local.exists():
        for src_name in ("arialbd.ttf", "arial.ttf"):
            src = Path(r"C:\Windows\Fonts") / src_name
            if src.exists():
                shutil.copy(src, local)
                break
        else:
            return ""
    return "arialbd.ttf"


# ── Segment builder ───────────────────────────────────────────────────────────

def build_segment(
    image: Path,
    text: str,
    duration: float,
    audio: Optional[Path],
    txt_file: Path,
    out: Path,
    font: str = "",
):
    """
    One segment: image → scale → zoompan → drawtext caption → encode.
    Audio is either the real TTS file or lavfi silence.
    """
    # When real audio drives the length, give zoompan generous headroom (30s cap).
    # When silence, use the exact default duration.
    frames = int(30 * FPS) if audio else int(duration * FPS)

    # Write caption to file — avoids ffmpeg filter escaping hell entirely
    wrapped = "\n".join(textwrap.wrap(text, width=CAPTION_WRAP_CHARS))
    txt_file.write_text(wrapped, encoding="utf-8")
    # Scale image to 2× output so zoompan has pixels to work with,
    # preserving aspect ratio and cropping to fill the frame.
    scale = (
        f"scale={RES_W * 2}:{RES_H * 2}"
        f":force_original_aspect_ratio=increase,"
        f"crop={RES_W * 2}:{RES_H * 2}"
    )
    zoompan = (
        f"zoompan="
        f"z='min(zoom+{ZOOM_SPEED},{MAX_ZOOM})':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={RES_W}x{RES_H}:fps={FPS}"
    )
    vf = f"{scale},{zoompan}"

    base = ["ffmpeg", "-y",
            "-loop", "1", "-i", str(image)]

    if audio:
        cmd = base + [
            "-i", str(audio),
            "-vf", vf,
            "-af", "loudnorm,apad=pad_dur=0.8",  # normalize loudness, then pad
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(out),
        ]
    else:
        cmd = base + [
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(out),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n[ffmpeg stderr — line {out.stem}]\n{result.stderr[-3000:]}")
        raise RuntimeError(f"Segment build failed: {out.stem}")


# ── Concat with crossfades ────────────────────────────────────────────────────

def concat_segments(segments: list, out: Path):
    if len(segments) == 1:
        shutil.copy(segments[0], out)
        return

    durations = [get_duration(s) for s in segments]
    inputs = []
    for s in segments:
        inputs += ["-i", str(s)]

    # Chain xfade (video) and acrossfade (audio) for each consecutive pair.
    # offset = cumulative time where next segment's fade starts.
    n = len(segments)
    vf_parts = []
    af_parts = []
    offset = 0.0
    prev_v = "[0:v]"
    prev_a = "[0:a]"

    for i in range(1, n):
        offset += durations[i - 1] - CROSSFADE_SECS
        is_last = (i == n - 1)
        v_out = "[vout]" if is_last else f"[v{i}]"
        a_out = "[aout]" if is_last else f"[a{i}]"
        vf_parts.append(
            f"{prev_v}[{i}:v]xfade=transition=fade"
            f":duration={CROSSFADE_SECS}:offset={offset:.3f}{v_out}"
        )
        af_parts.append(
            f"{prev_a}[{i}:a]acrossfade=d={AUDIO_XFADE_SECS}{a_out}"
        )
        prev_v = v_out
        prev_a = a_out

    fc = ";".join(vf_parts + af_parts)
    cmd = (
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc,
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "fast",
         "-c:a", "aac",
         "-pix_fmt", "yuv420p",
         str(out)]
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n[ffmpeg stderr — concat]\n{result.stderr[-3000:]}")
        raise RuntimeError("Concat failed")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    check_deps()

    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")
    if not IMAGES_DIR.exists():
        sys.exit(f"[ERROR] {IMAGES_DIR}/ folder not found — create it and add images named 1.jpg, 2.jpg, ...")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    title  = script.get("title", "(no title)")
    print(f"\nScript: '{title}' — {len(lines)} lines\n")

    font = setup_font()

    TMP_SEGS.mkdir(exist_ok=True)
    TMP_TXT.mkdir(exist_ok=True)

    segments = []
    for line in lines:
        lid  = line["id"]
        text = line["text"]

        image = find_file(IMAGES_DIR, str(lid), IMAGE_EXTS)
        if not image:
            print(f"  [SKIP] line {lid}: no image found in {IMAGES_DIR}/")
            continue

        audio    = find_file(AUDIO_DIR, str(lid), AUDIO_EXTS) if AUDIO_DIR.exists() else None
        duration = get_duration(audio) if audio else DEFAULT_LINE_SECS

        seg_out  = TMP_SEGS / f"seg_{lid:03d}.mp4"
        txt_file = TMP_TXT  / f"line_{lid:03d}.txt"

        src_label = audio.name if audio else f"silence ({DEFAULT_LINE_SECS}s)"
        print(f"  Line {lid:2d} [{duration:.1f}s | {src_label}]")
        print(f"         {text[:70]}{'...' if len(text) > 70 else ''}")

        build_segment(image, text, duration, audio, txt_file, seg_out, font)
        segments.append(seg_out)

    if not segments:
        sys.exit("\n[ERROR] No segments built — check that images/ has files named 1.jpg, 2.jpg, ...")

    print(f"\nConcatenating {len(segments)} segments with {CROSSFADE_SECS}s crossfades...")
    concat_segments(segments, OUTPUT)

    shutil.rmtree(TMP_SEGS, ignore_errors=True)
    shutil.rmtree(TMP_TXT,  ignore_errors=True)

    print(f"\nDone → {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
