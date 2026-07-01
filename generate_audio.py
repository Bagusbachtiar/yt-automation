#!/usr/bin/env python3
"""
Generate per-line TTS audio from script.json using Kokoro TTS (kokoro-onnx).
Outputs: audio/1.wav, audio/2.wav, ... (one file per script line)

Run: python generate_audio.py
"""

import json
import sys
import soundfile as sf
from pathlib import Path

SCRIPT_JSON = Path("script.json")
AUDIO_DIR   = Path("audio")
VOICE       = "af_heart"   # primary — swap to "af_bella" if this sounds off
SPEED       = 1.0
LANG        = "en-us"

MODEL_FILE  = Path("kokoro-v1.0.int8.onnx")   # 88MB int8 — fastest, good quality
VOICES_FILE = Path("voices-v1.0.bin")

_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_DOWNLOADS = {
    MODEL_FILE:  f"{_BASE_URL}/kokoro-v1.0.int8.onnx",
    VOICES_FILE: f"{_BASE_URL}/voices-v1.0.bin",
}


def download_models():
    """Download Kokoro model files from GitHub releases if not present."""
    import urllib.request

    for dest, url in _DOWNLOADS.items():
        if dest.exists():
            continue
        print(f"Downloading {dest.name} ...")
        try:
            urllib.request.urlretrieve(url, dest)
            print(f"  → {dest} saved")
        except Exception as e:
            sys.exit(f"[ERROR] Download failed for {dest.name}: {e}")


def main():
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        sys.exit("[ERROR] kokoro-onnx not installed. Run: pip install kokoro-onnx soundfile")

    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")

    download_models()

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    print(f"\nGenerating TTS for {len(lines)} lines | voice: {VOICE}\n")

    AUDIO_DIR.mkdir(exist_ok=True)

    print("Loading Kokoro model...")
    kokoro = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
    print("Model ready.\n")

    for line in lines:
        lid  = line["id"]
        text = line["text"]
        out  = AUDIO_DIR / f"{lid}.wav"

        print(f"  Line {lid:2d}: {text[:65]}{'...' if len(text) > 65 else ''}")
        samples, sample_rate = kokoro.create(text, voice=VOICE, speed=SPEED, lang=LANG)
        sf.write(str(out), samples, sample_rate)
        duration = len(samples) / sample_rate
        print(f"          → {out}  ({duration:.2f}s)")

    print(f"\nDone. Run: python assemble.py")


if __name__ == "__main__":
    main()
