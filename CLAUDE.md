# YouTube Shorts Automation — Project Reference

## Goal
n8n automation that takes a typed topic idea and produces a finished YouTube Short: AI-written script → text-to-speech → matched stock images → assembled video → uploaded to YouTube. Two human-in-the-loop checkpoints (script review, image review) via Telegram, not a web dashboard (see non-goals). Single channel/genre for now; architecture shouldn't hardcode this so a second channel/genre can be added later without a rewrite.

Serves two purposes: an Upwork portfolio piece demonstrating automation/AI-integration skills, and a potential real content pipeline if it performs.

## Stack
- **n8n** — orchestrates the workflow (runs locally on Windows, Docker, LAN IP 192.168.100.4)
- **Ollama, `llama3:latest` (8B, base Llama 3, not 3.3)** — script generation. Tested and validated across 3 iterations on "cows and methane emissions." Do not upgrade to llama3.3 unless new failures appear on more topics.
- **Kokoro TTS (Kokoro-82M, Apache 2.0, self-hosted, free, commercial use allowed)** — voice: `af_heart`. Built and working. Chosen over Piper (quality), ElevenLabs and Amazon Polly (both require a card on file; ElevenLabs free tier also bans commercial use).
- **Pexels / Pixabay API** — PRIMARY image sources for Animals & Ocean Life niche. Deep wildlife/ocean photography coverage, no license-filtering complexity, no rate-limit issues. Pexels first, Pixabay fallback. Built and working.
- **ffmpeg** — assembly: image timed to audio duration per line, Ken Burns zoompan, crossfades, word-by-word captions via faster-whisper. Built and working — `output.mp4` exists.
- **YouTube Data API v3** — upload with title/description/tags. Not yet built. Quota: 10k units/day, one upload ≈ 1600 units.
- **Telegram** (n8n native node) — review/approval for both checkpoints, using n8n's wait-for-webhook pause/resume pattern. Not yet built.

Deferred to v2 (explicitly not now): Next.js 16 + Tailwind v4 + Prisma + PostgreSQL (Neon) + Cloudinary web dashboard as a nicer review UI. Do not build until the single-channel pipeline ships a working video end-to-end and Telegram's limitations are actually felt in practice — building it earlier means designing the review UI blind, before knowing what it needs to do.

## Tested prompt template (script_prompt_template.txt)
Iterated through multiple rounds of real testing. Reliably produces valid JSON, avoids fabricated/contested statistics, avoids cross-mechanism causal errors, and produces relevant/searchable image keywords. Full template and the specific failures each rule fixes are in `script_prompt_template.txt` in this directory — read it before modifying the prompt logic.

**Not yet tested on Animals & Ocean Life topics.** Run 2-3 topics through full pipeline before trusting unattended runs.

## Explicit non-goals / decided already (don't relitigate)
- No AI-generated video — Sora is dead (shut down April 2026); real stock footage/images only.
- No clipping/repurposing other creators' content — copyright risk. Original AI-scripted narration over stock imagery only.
- No desktop app.
- No Claude API/subscription inside the automation — Claude's role is one-time prompt template design and ad hoc human assistance, not a per-video call. Keeps the pipeline zero-marginal-cost via Ollama.
- No web dashboard until v2, and only after Telegram's limitations are felt in real use — see Stack section.
- Multi-channel/multi-genre is a future goal, not current scope — validate one channel first.
- **Niche is Animals & Ocean Life (first channel).** Wildlife facts, animal behavior, deep-sea creatures, adaptations, etc. Photo-compatible, concept-driven. Pipeline outputs static images with Ken Burns zoom — not video-native content. Pexels/Pixabay have deep coverage for this niche — the multi-source archive fallback chain built for history (Commons, Flickr, LoC, NASA) is unnecessary complexity here unless testing reveals real gaps.

## Known issues
- **Monetization/demonetization risk**: AI-narrated, stock-image content sits in the category YouTube has tightened demonetization rules around since 2025. Not fixable by better prompts — a content-format risk to monitor once live.
- **Image relevance isn't perfect** even after prompt iterations — expected to always need the human review checkpoint, not something to fully engineer away.

## History niche — explored, deprioritized (not deleted, may revisit as second channel)

History was the original first channel. Switched to Animals & Ocean Life because history's image sourcing kept hitting practical walls: thin coverage on specific historical moments, Wikimedia rate limiting (429s on every run with >8 lines), license filtering overhead across multiple archive APIs (Commons, Flickr, LoC, NASA). Animals & Ocean Life has none of these problems with Pexels/Pixabay.

**What was built for history (all still in codebase, not removed):**
- `wikipedia_fetch.py` — fuzzy Wikipedia search → full article text → injected as grounding reference into prompt
- `generate_script.py` — script generator that calls wikipedia_fetch first, then injects into prompt
- `fetch_images.py` — multi-source fallback: Wikipedia article pool (keyword-matched, not round-robin), per-line `wiki_keyword` search, Flickr Commons, Wikimedia Commons, Pexels/Pixabay last
- `review_images.py` — pool-based Telegram review (up to 20 images at once, numbered, assign per line)
- Relevance filter (`_relevant_overlap`) — rejects Wikipedia article matches with insufficient term overlap
- Fallback keyword (`image_keyword_fallback`) — broader keyword for lines where specific event has thin photo coverage
- `script_prompt_template.txt` — rules for historical accuracy: grounding-only claims, no fabricated stats, no loose causal attribution, period-specific + unambiguous image keywords

**Key learnings:**
- Pexels/Pixabay are wrong for history — stock sites return modern generic photos for historical keywords
- Wikimedia 429s are a real wall: even 2s delays between requests get rate-limited at >8 lines
- Per-line Wikipedia keyword search was the right idea but consistently hit rate limits before populating enough candidates
- Round-robin Wikipedia article image assignment was wrong — images weren't matched to line content; fixed to keyword-scored assignment before deprioritizing history
- History can still work as a second channel with a Flickr PRO key ($) or by spacing requests far apart; not a dead end, just not the fast path
