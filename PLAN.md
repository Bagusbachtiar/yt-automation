# Project Status

## Done
- [x] Script generation — Ollama + `llama3:latest` + tested prompt template → `script.json`
- [x] Image fetch — `fetch_images.py`, Pexels → Pixabay fallback → `images/`
- [x] Audio (TTS) — `generate_audio.py`, Kokoro `af_heart` → `audio/`
- [x] Video assembly — `assemble.py`, ffmpeg, Ken Burns zoom + crossfades → `output.mp4`
- [x] Captions — word-by-word via faster-whisper on full concatenated audio, Arial Black, bottom-center, 1s gap between TTS sentences, no fade-in on speech

## In progress
- [x] Wikipedia-grounded script generation — `wikipedia_fetch.py` + `generate_script.py`, fuzzy search → full article text → injected into prompt
- [x] Wikimedia Commons image sourcing — first source, 1080px thumbnails, license filter (public domain/CC0/CC-BY), Pexels/Pixabay fallback

## Not started
- [ ] Telegram approval checkpoints (script review, image review) — n8n wait-for-webhook pattern
- [ ] YouTube Data API v3 upload step — quota 10k units/day, one upload ≈ 1600 units
- [ ] Full n8n orchestration wiring all steps together (currently standalone scripts, not triggered by n8n end-to-end)
- [ ] Web dashboard (deliberately deferred to v2)

## Next session should start here
Wikipedia pipeline done and tested on "The Fall of the Berlin Wall." Run order is now: `python generate_script.py "topic"` → `python fetch_images.py` → `python generate_audio.py` → `python assemble.py`.
Next: YouTube Data API v3 upload (`upload.py`), then Telegram approval checkpoints, then n8n orchestration.
