# Project Status

## Done
- [x] Script generation — Ollama + `llama3:latest` + tested prompt template → `script.json`
- [x] Image fetch — `fetch_images.py`, Pexels → Pixabay fallback → `images/`
- [x] Audio (TTS) — `generate_audio.py`, Kokoro `af_heart` → `audio/`
- [x] Video assembly — `assemble.py`, ffmpeg, Ken Burns zoom + crossfades → `output.mp4`
- [x] Captions — word-by-word via faster-whisper on full concatenated audio, Arial Black, bottom-center, 1s gap between TTS sentences, no fade-in on speech
- [x] Pool-based Telegram image review — `review_images.py`, up to 20 images sent at once numbered, user assigns per line by number
- [x] Multi-source image fetching — Wikipedia article pool (keyword-matched), per-line wiki_keyword search, Flickr Commons, Commons search, Pexels/Pixabay fallback
- [x] Relevance filter + fallback keyword support in `fetch_images.py`

## Not started
- [ ] **Test prompt template on 2-3 Animals & Ocean Life topics** — e.g. "how octopuses camouflage," "why some deep-sea fish glow," "how elephants communicate over long distances." Confirm script quality + image keyword specificity before trusting unattended runs.
- [ ] **Confirm Pexels/Pixabay coverage** is strong enough for Animals & Ocean Life without needing Commons/archive fallbacks. Check image relevance after review step.
- [ ] Telegram approval checkpoint for **script review** (image review already built via `review_images.py`)
- [ ] YouTube Data API v3 upload step (`upload.py`) — quota 10k units/day, one upload ≈ 1600 units
- [ ] Full n8n orchestration wiring all steps together (currently standalone scripts, not triggered by n8n end-to-end)
- [ ] Web dashboard (deliberately deferred to v2)

## Next session should start here
Niche pivoted to Animals & Ocean Life. Run order: `python generate_script.py "topic"` → `python fetch_images.py` → `python review_images.py` → `python generate_audio.py` → `python assemble.py`.
Test 2-3 animal/ocean topics end-to-end. After image review, check: are Pexels/Pixabay results relevant without needing archive sources? If yes, pipeline is ready for upload step.

## Parked — history niche exploration (revisit if history becomes second channel)
- [x] Wikipedia-grounded script generation — `wikipedia_fetch.py` + `generate_script.py`, fuzzy search → full article text → injected into prompt
- [x] Wikimedia Commons image sourcing — license filter (public domain/CC0/CC-BY), 1080px thumbnails
- [x] Per-line Wikipedia keyword search with relevance filter
- [x] Keyword-scored Wikipedia article pool (replaced round-robin)
- [x] Flickr Commons search (code built, no free API key available — Flickr requires PRO)
- [x] LoC search (code built, times out from user's network — removed from active pipeline)
- [ ] NASA Image Library API — would solve coverage for space topics, never built (deprioritized with niche switch)
- [ ] Europeana API — would cover European history, never built (deprioritized with niche switch)
- [ ] Flickr PRO key — $$ required for API access; blocks Flickr Commons route for now
