# Project Status

## Done
- [x] Script generation — Ollama + `llama3:latest` + tested prompt template → `script.json`
- [x] Image fetch — `fetch_images.py`, Pexels → Pixabay fallback → `images/`
- [x] Audio (TTS) — `generate_audio.py`, Kokoro `af_heart` → `audio/`
- [x] Video assembly — `assemble.py`, ffmpeg, Ken Burns zoom + crossfades → `output.mp4`
- [x] Captions — word-by-word via faster-whisper on full concatenated audio, Arial Black, bottom-center, 1s gap between TTS sentences, no fade-in on speech

## In progress
- [ ] Wikipedia-grounded script generation — `wikipedia_fetch.py`, fuzzy search → article summary → injected into prompt as grounded reference
- [ ] Wikimedia Commons image sourcing — history-niche primary source, public domain/CC0/CC-BY only, Pexels/Pixabay fallback

## Not started
- [ ] Telegram approval checkpoints (script review, image review) — n8n wait-for-webhook pattern
- [ ] YouTube Data API v3 upload step — quota 10k units/day, one upload ≈ 1600 units
- [ ] Full n8n orchestration wiring all steps together (currently standalone scripts, not triggered by n8n end-to-end)
- [ ] Web dashboard (deliberately deferred to v2)

## Next session should start here
Build `wikipedia_fetch.py` (Wikipedia fuzzy search → article text) and update `script_prompt_template.txt` to inject it as reference. Then update `fetch_images.py` to try Wikimedia Commons first. Test end-to-end on "The Fall of the Berlin Wall."
