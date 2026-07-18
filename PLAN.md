# Project Status

## Done
- [x] Script generation — Ollama + `llama3:latest` + tested prompt template → `script.json`
- [x] Image fetch — `fetch_images.py`, Pexels → Pixabay fallback → `images/`
- [x] Audio (TTS) — `generate_audio.py`, Kokoro `af_heart` → `audio/`
- [x] Video assembly — `assemble.py`, ffmpeg, Ken Burns zoom + crossfades → `output.mp4`

## In progress
- [ ] Caption fix — root cause identified (apad + raw-duration timing mismatch causing drift). Captions currently removed. Next: remove `apad`, verify crossfades cover the gap, rebuild ASS captions with correct cumulative timing, then chunk each line into 2-4 shorter caption segments (not one static block per line) using proportional-by-character-count duration split. No Whisper.

## Not started
- [ ] Telegram approval checkpoints (script review, image review) — n8n wait-for-webhook pattern, not yet wired
- [ ] YouTube Data API v3 upload step
- [ ] Wikipedia-grounded scripts for history-niche content (see CLAUDE.md "History-niche extension")
- [ ] Wikimedia Commons image sourcing for history content
- [ ] Full n8n orchestration wiring all steps together (currently these are standalone scripts run manually, not yet triggered by n8n end-to-end)
- [ ] Web dashboard (deliberately deferred to v2 — do not start until Telegram checkpoints are live and their limitations are actually felt)

## Next session should start here
Fix the caption bug (see "In progress" above) — this was the active blocker before this session. Once captions work reliably on the cow/methane test case, move to wiring the Telegram checkpoints so the pipeline can run as one triggered flow instead of manually-run scripts.
