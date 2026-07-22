#!/usr/bin/env python3
"""
YouTube Shorts assembly — ffmpeg only, no cloud deps.

Expected layout:
  script.json      — JSON from Ollama (see script_prompt_template.txt)
  images/1.jpg     — one image per line ID (jpg/jpeg/png/webp accepted)
  audio/1.wav      — OPTIONAL per-line TTS audio; absent = DEFAULT_LINE_SECS silence

Output:
  output.mp4       — 1080x1920 portrait, H.264, AAC, word-by-word captions, Ken Burns zoom, crossfades

Run:
  python assemble.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
RES_W, RES_H = 1080, 1920
FPS = 30
CROSSFADE_SECS = 0.5
AUDIO_XFADE_SECS = 0.5
SEGMENT_PAD_SECS = 1.0
DEFAULT_LINE_SECS = 5.0
FONT_NAME = "Arial Black"
FONT_SIZE = 90
CAPTION_MARGIN_V = 260
ZOOM_SPEED = 0.0015
MAX_ZOOM = 1.5

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_JSON  = Path("script.json")
IMAGES_DIR   = Path("images")
AUDIO_DIR    = Path("audio")
OUTPUT       = Path("output.mp4")
TMP_SEGS     = Path("tmp_segments")
TMP_CONCAT   = Path("tmp_concat.mp4")
CAPTIONS_ASS = Path("captions.ass")

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


def load_whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("[ERROR] faster-whisper not installed. Run: pip install faster-whisper")
    print("Loading Whisper model...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    print("Whisper ready.\n")
    return model


def extract_audio(video: Path, out: Path):
    """Extract audio from video to WAV for Whisper."""
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vn", "-acodec", "pcm_s16le", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Audio extraction failed")


def transcribe_words(model, audio: Path) -> list:
    """Return [(word, start_sec, end_sec), ...] from audio file."""
    segments, _ = model.transcribe(str(audio), word_timestamps=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            word = w.word.strip().replace(',', '').replace('.', '').strip('-')
            if word:
                words.append((w.start, w.end, word))
    return words


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_ass(word_events: list):
    """word_events = [(abs_start, abs_end, word), ...] across whole video."""
    dialogue_lines = []
    for start, end, word in word_events:
        s = format_ass_time(start)
        e = format_ass_time(end)
        dialogue_lines.append(
            f"Dialogue: 0,{s},{e},Caption,,0,0,0,,{{\\fad(40,0)}}{word}"
        )

    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {RES_W}\n"
        f"PlayResY: {RES_H}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{FONT_NAME},{FONT_SIZE},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"1,0,0,0,100,100,2,0,1,6,3,2,60,60,{CAPTION_MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(dialogue_lines) + "\n"
    )
    CAPTIONS_ASS.write_text(content, encoding="utf-8")


# ── Segment builder ───────────────────────────────────────────────────────────

def build_segment(
    image: Path,
    duration: float,
    audio: Optional[Path],
    out: Path,
    zoom_out: bool = False,
    pad_audio: bool = True,
    leading_silence: float = 0.0,
):
    pad = SEGMENT_PAD_SECS if (audio and pad_audio) else 0.0
    effective_duration = leading_silence + duration + pad
    frames = int(effective_duration * FPS)

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
    vf = f"{scale},{zoompan}"

    base = ["ffmpeg", "-y", "-loop", "1", "-r", str(FPS), "-i", str(image)]

    if audio:
        cmd = base + [
            "-i", str(audio),
            "-vf", vf,
            "-af", ",".join(filter(None, [
                f"adelay={int(leading_silence*1000)}|{int(leading_silence*1000)}" if leading_silence else None,
                "loudnorm",
                f"apad=pad_dur={SEGMENT_PAD_SECS}" if pad_audio else None,
            ])),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100",
            "-t", str(effective_duration),
            "-pix_fmt", "yuv420p",
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
        print(f"\n[ffmpeg stderr — {out.stem}]\n{result.stderr[-3000:]}")
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


def burn_captions(video_in: Path, ass_file: Path, out: Path):
    ass_path = str(ass_file).replace("\\", "/")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_in),
        "-vf", f"ass='{ass_path}'",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n[ffmpeg stderr — burn_captions]\n{result.stderr[-3000:]}")
        raise RuntimeError("Caption burn failed")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    check_deps()

    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")
    if not IMAGES_DIR.exists():
        sys.exit(f"[ERROR] {IMAGES_DIR}/ not found — add images named 1.jpg, 2.jpg, ...")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    title  = script.get("title", "(no title)")
    print(f"\nScript: '{title}' — {len(lines)} lines\n")

    TMP_SEGS.mkdir(exist_ok=True)

    # Collect valid entries first so we know which is last (for apad skip)
    entries = []
    for line in lines:
        lid   = line["id"]
        text  = line["text"]
        image = find_file(IMAGES_DIR, str(lid), IMAGE_EXTS)
        if not image:
            print(f"  [SKIP] line {lid}: no image in {IMAGES_DIR}/")
            continue
        audio    = find_file(AUDIO_DIR, str(lid), AUDIO_EXTS) if AUDIO_DIR.exists() else None
        duration = get_duration(audio) if audio else DEFAULT_LINE_SECS
        entries.append((lid, text, image, audio, duration))

    segments = []

    for idx, (lid, text, image, audio, duration) in enumerate(entries):
        is_last = (idx == len(entries) - 1)
        src = audio.name if audio else f"silence ({DEFAULT_LINE_SECS}s)"
        print(f"  Line {lid:2d} [{duration:.1f}s | {src}]")
        print(f"         {text[:80]}{'...' if len(text) > 80 else ''}")

        seg_out = TMP_SEGS / f"seg_{lid:03d}.mp4"
        leading = 0.0 if idx == 0 else CROSSFADE_SECS
        build_segment(image, duration, audio, seg_out, zoom_out=(idx % 2 == 1), pad_audio=not is_last, leading_silence=leading)
        segments.append(seg_out)

    if not segments:
        sys.exit("\n[ERROR] No segments built — check images/ has files named 1.jpg, 2.jpg, ...")

    print(f"\nConcatenating {len(segments)} segments with {CROSSFADE_SECS}s crossfades...")
    concat_segments(segments, TMP_CONCAT)

    print("Transcribing word timestamps from final audio...")
    tmp_audio = Path("tmp_audio.wav")
    extract_audio(TMP_CONCAT, tmp_audio)
    whisper = load_whisper()
    word_events = transcribe_words(whisper, tmp_audio)
    tmp_audio.unlink(missing_ok=True)

    print("Burning captions...")
    build_ass(word_events)
    burn_captions(TMP_CONCAT, CAPTIONS_ASS, OUTPUT)

    TMP_CONCAT.unlink(missing_ok=True)
    CAPTIONS_ASS.unlink(missing_ok=True)
    shutil.rmtree(TMP_SEGS, ignore_errors=True)

    print(f"\nDone -> {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
