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
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    from faster_whisper import WhisperModel as _WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

_whisper_model = None

# ── Config ────────────────────────────────────────────────────────────────────
RES_W, RES_H = 1080, 1920          # portrait 9:16 for Shorts
FPS = 30
CROSSFADE_SECS = 0.5               # video crossfade between segments
AUDIO_XFADE_SECS = 0.05           # audio crossfade — near-instant to avoid volume ramp
DEFAULT_LINE_SECS = 5.0            # seconds per line when no audio provided
FONT_SIZE = 64
CAPTION_MARGIN_V = 320             # px from bottom
ZOOM_SPEED = 0.0015                # Ken Burns zoom increment per frame
MAX_ZOOM = 1.5

CLAUSE_BREAK_RE = re.compile(r'[,;—-]| and | but | so | because | which | when ')

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


def find_images_for_line(directory: Path, lid: int) -> list:
    """Return list of image paths for a line. Supports {id}_1.jpg, {id}_2.jpg, ... and legacy {id}.jpg."""
    images = []
    for i in range(1, 10):
        p = find_file(directory, f"{lid}_{i}", IMAGE_EXTS)
        if p:
            images.append(p)
        else:
            break
    if not images:
        p = find_file(directory, str(lid), IMAGE_EXTS)
        if p:
            images.append(p)
    return images


def clause_split_fraction(text: str) -> float:
    """Find a natural clause break near the middle of text. Returns fraction (0-1) of chars before break."""
    n = len(text)
    if n == 0:
        return 0.5
    mid = n // 2
    candidates = [m.start() for m in CLAUSE_BREAK_RE.finditer(text)]
    if candidates:
        pos = min(candidates, key=lambda p: abs(p - mid))
    else:
        left = text.rfind(' ', 0, mid)
        right = text.find(' ', mid)
        pos = left if left != -1 else (right if right != -1 else mid)
    frac = pos / n
    return min(max(frac, 0.25), 0.75)  # clamp so no chunk is too short


def split_audio(audio: Path, n: int, tmp_dir: Path, lid: int, fractions=None) -> list:
    """Split audio into n chunks by given fractions (defaults to equal). Returns list of chunk paths."""
    if n == 1:
        return [audio]
    total = get_duration(audio)
    if not fractions:
        fractions = [1.0 / n] * n
    chunks = []
    cursor = 0.0
    for i, frac in enumerate(fractions):
        dur = total * frac
        out = tmp_dir / f"audio_{lid:03d}_{i+1}.wav"
        cmd = ["ffmpeg", "-y", "-i", str(audio),
               "-ss", f"{cursor:.4f}",
               "-t",  f"{dur:.4f}",
               str(out)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Audio split failed for line {lid} chunk {i+1}")
        chunks.append(out)
        cursor += dur
    return chunks


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        print("  [Whisper] loading tiny model (first run only)...")
        _whisper_model = _WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


def get_word_timestamps(audio: Path) -> list:
    """Returns [(word, start_sec, end_sec), ...] or [] on failure."""
    if not _WHISPER_AVAILABLE:
        return []
    try:
        model = _get_whisper()
        segments, _ = model.transcribe(str(audio), word_timestamps=True, language="en")
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append((w.word.strip(), w.start, w.end))
        return words
    except Exception as e:
        print(f"    [Whisper] failed: {e} — falling back to char-proportional")
        return []


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_ass(text: str, duration: float, ass_file: Path, font_name: str, word_times=None):
    """Word-by-word karaoke-highlight caption. Uses Whisper timestamps when available."""
    if word_times:
        parts = []
        cursor = 0.0
        for word, start, end in word_times:
            if start > cursor + 0.02:
                gap_centis = max(1, int((start - cursor) * 100))
                parts.append(f"{{\\k{gap_centis}}} ")
            word_centis = max(1, int((end - start) * 100))
            parts.append(f"{{\\k{word_centis}}}{word} ")
            cursor = end
        if cursor < duration - 0.05:
            parts.append(f"{{\\k{max(1, int((duration - cursor) * 100))}}} ")
        dialogue_text = "".join(parts).strip()
    else:
        words = text.split()
        total_chars = sum(len(w) for w in words) or 1
        parts = []
        for w in words:
            frac = len(w) / total_chars
            centis = max(1, int(duration * frac * 100))
            parts.append(f"{{\\k{centis}}}{w} ")
        dialogue_text = "".join(parts).strip()
    end_ts = format_ass_time(duration)

    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {RES_W}
PlayResY: {RES_H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{FONT_SIZE},&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,60,60,{CAPTION_MARGIN_V},0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{end_ts},Caption,,0,0,0,,{dialogue_text}
"""
    ass_file.write_text(content, encoding="utf-8")


# ── Segment builder ───────────────────────────────────────────────────────────

def build_segment(
    image: Path,
    text: str,
    duration: float,
    audio: Optional[Path],
    ass_file: Path,
    out: Path,
    zoom_out: bool = False,
):
    """
    One segment: image → scale → zoompan → burned-in karaoke captions → encode.
    Audio is either the real TTS file or lavfi silence.
    """
    # When real audio drives the length, give zoompan generous headroom (30s cap).
    # When silence, use the exact default duration.
    frames = int(30 * FPS) if audio else int(duration * FPS)

    word_times = get_word_timestamps(audio) if audio else []
    build_ass(text, duration, ass_file, "Arial", word_times or None)
    # ASS filter needs forward slashes — backslash is a filter-string escape char
    ass_path = str(ass_file).replace("\\", "/")

    # Scale image to 2× output so zoompan has pixels to work with,
    # preserving aspect ratio and cropping to fill the frame.
    scale = (
        f"scale={RES_W * 2}:{RES_H * 2}"
        f":force_original_aspect_ratio=increase,"
        f"crop={RES_W * 2}:{RES_H * 2}"
    )
    if zoom_out:
        zoom_expr = f"if(eq(on,1),{MAX_ZOOM},max(zoom-{ZOOM_SPEED},1))"
    else:
        zoom_expr = f"min(zoom+{ZOOM_SPEED},{MAX_ZOOM})"
    zoompan = (
        f"zoompan="
        f"z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={RES_W}x{RES_H}:fps={FPS}"
    )
    vf = f"{scale},{zoompan},ass='{ass_path}'"

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

    TMP_SEGS.mkdir(exist_ok=True)
    TMP_TXT.mkdir(exist_ok=True)

    segments = []
    seg_counter = 0
    for line in lines:
        lid  = line["id"]
        text = line["text"]

        images = find_images_for_line(IMAGES_DIR, lid)
        if not images:
            print(f"  [SKIP] line {lid}: no image found in {IMAGES_DIR}/")
            continue

        audio = find_file(AUDIO_DIR, str(lid), AUDIO_EXTS) if AUDIO_DIR.exists() else None
        total_duration = get_duration(audio) if audio else DEFAULT_LINE_SECS

        src_label = audio.name if audio else f"silence ({DEFAULT_LINE_SECS}s)"
        print(f"  Line {lid:2d} [{total_duration:.1f}s | {src_label} | {len(images)} image{'s' if len(images)>1 else ''}]")
        print(f"         {text[:70]}{'...' if len(text) > 70 else ''}")

        # Split text at a natural clause break so image switch lines up with topic shift
        if len(images) == 2:
            frac = clause_split_fraction(text)
            fractions = [frac, 1 - frac]
            split_idx = int(len(text) * frac)
            texts = [text[:split_idx].strip(), text[split_idx:].strip()]
        else:
            fractions = None
            texts = [text] * len(images)

        audio_chunks = split_audio(audio, len(images), TMP_SEGS, lid, fractions) if audio else [None] * len(images)

        for i, (image, aud_chunk, seg_text) in enumerate(zip(images, audio_chunks, texts), start=1):
            duration = get_duration(aud_chunk) if aud_chunk else total_duration / len(images)
            seg_out  = TMP_SEGS / f"seg_{lid:03d}_{i:02d}.mp4"
            ass_file = TMP_TXT  / f"line_{lid:03d}_{i:02d}.ass"
            build_segment(image, seg_text or text, duration, aud_chunk, ass_file, seg_out, zoom_out=(seg_counter % 2 == 1))
            segments.append(seg_out)
            seg_counter += 1

    if not segments:
        sys.exit("\n[ERROR] No segments built — check that images/ has files named 1.jpg, 2.jpg, ...")

    print(f"\nConcatenating {len(segments)} segments with {CROSSFADE_SECS}s crossfades...")
    concat_segments(segments, OUTPUT)

    shutil.rmtree(TMP_SEGS, ignore_errors=True)
    shutil.rmtree(TMP_TXT,  ignore_errors=True)

    print(f"\nDone → {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
