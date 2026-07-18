# YouTube Shorts Automation — Project Reference

## Goal
n8n automation that takes a typed topic idea and produces a finished YouTube Short: AI-written script → text-to-speech → matched stock images → assembled video → uploaded to YouTube. Two human-in-the-loop checkpoints (script review, image review) via Telegram, not a web dashboard (see non-goals). Single channel/genre for now; architecture shouldn't hardcode this so a second channel/genre can be added later without a rewrite.

Serves two purposes: an Upwork portfolio piece demonstrating automation/AI-integration skills, and a potential real content pipeline if it performs.

## Stack
- **n8n** — orchestrates the workflow (runs locally on Windows, Docker, LAN IP 192.168.100.4)
- **Ollama, `llama3:latest` (8B, base Llama 3, not 3.3)** — script generation. Tested and validated across 3 iterations on "cows and methane emissions." Do not upgrade to llama3.3 unless new failures appear on more topics.
- **Kokoro TTS (Kokoro-82M, Apache 2.0, self-hosted, free, commercial use allowed)** — voice: `af_heart`. Built and working. Chosen over Piper (quality), ElevenLabs and Amazon Polly (both require a card on file; ElevenLabs free tier also bans commercial use).
- **Pexels / Pixabay API** — stock image search via `image_keyword` field, Pexels first, Pixabay fallback. Built and working.
- **ffmpeg** — assembly: image timed to audio duration per line, Ken Burns zoompan, crossfades. Built — `output.mp4` exists. Captions currently removed (see Known Issues).
- **YouTube Data API v3** — upload with title/description/tags. Not yet built. Quota: 10k units/day, one upload ≈ 1600 units.
- **Telegram** (n8n native node) — review/approval for both checkpoints, using n8n's wait-for-webhook pause/resume pattern. Not yet built.

Deferred to v2 (explicitly not now): Next.js 16 + Tailwind v4 + Prisma + PostgreSQL (Neon) + Cloudinary web dashboard as a nicer review UI. Do not build until the single-channel pipeline ships a working video end-to-end and Telegram's limitations are actually felt in practice — building it earlier means designing the review UI blind, before knowing what it needs to do.

## Tested prompt template (script_prompt_template.txt)
Iterated through 3 rounds of real testing against `llama3:latest`. Reliably produces valid JSON, avoids fabricated/contested statistics, avoids cross-mechanism causal errors, and produces relevant/searchable image keywords. Full template and the specific failures each rule fixes are in `script_prompt_template.txt` in this directory — read it before modifying the prompt logic.

**Not yet tested**: whether the template holds on topics further from "cows and methane." Test varied topics before trusting unattended runs.

## Explicit non-goals / decided already (don't relitigate)
- No AI-generated video — Sora is dead (shut down April 2026); real stock footage/images only.
- No clipping/repurposing other creators' content — copyright risk. Original AI-scripted narration over stock imagery only.
- No desktop app.
- No Claude API/subscription inside the automation — Claude's role is one-time prompt template design and ad hoc human assistance, not a per-video call. Keeps the pipeline zero-marginal-cost via Ollama.
- No web dashboard until v2, and only after Telegram's limitations are felt in real use — see Stack section.
- Multi-channel/multi-genre is a future goal, not current scope — validate one channel first.
- Niche is photo-compatible, concept-driven content (psychology, history, money, health) — not video-native content (footage, ambience, gameplay) — chosen deliberately because the pipeline outputs static images with Ken Burns zoom, not video clips. Don't chase free AI-video generators to expand niche options; it reintroduces cost/quota risk to solve a problem this niche list doesn't have.

## Known issues
- **Caption sync (root cause found, fix pending)**: `apad=pad_dur=0.8` in `assemble.py` added 0.8s per segment, but caption timing math used raw audio duration — drift compounds per segment. Captions removed locally, not yet re-added. Fix (Option A): remove `apad` (crossfades should cover the gap — verify this before assuming it), then rebuild ASS caption code using `get_duration(audio)`. When captions are re-added: also fix caption chunking — one line's full ~20-word text as a single static block reads as "out of sync" even with correct timing, because the full text is visible while TTS is still speaking through it. Fix: split each line into 2-4 chunks at natural breakpoints (commas, "and," "but," "because"), distribute that line's known duration across chunks proportionally by character count. No Whisper needed for this — use durations already known from Kokoro's output.
- **Monetization/demonetization risk**: AI-narrated, stock-image content sits in the category YouTube has tightened demonetization rules around since 2025. Not fixable by better prompts — a content-format risk to monitor once live.
- **Image relevance isn't perfect** even after 3 prompt iterations — expected to always need the human review checkpoint, not something to fully engineer away.

## History-niche extension (planned, not started)
For history topics specifically: ground the script in a real source instead of Ollama's memory, since Ollama can misstate historical facts same as it invented the cattle-emissions stat.
- **Script grounding**: n8n → Wikipedia API (fuzzy search endpoint, not exact-title lookup) → pull article summary/relevant section as plain text → pass to Ollama as reference material alongside the existing prompt template, with an added rule: "do not include any claim not explicitly stated in the reference text below." Do not feed raw Wikipedia text as narration directly — that's reproducing copyrighted text, not writing original script.
- **Image sourcing**: Wikimedia Commons API (sister project to Wikipedia, has actual period photos/paintings, unlike Pexels/Pixabay which only have modern photos of historical sites). Filter to public domain / CC0 / CC-BY only — Commons hosts mixed licenses, some require attribution or ban commercial use. Fallback chain: Commons first for history content, Pexels/Pixabay fallback if Commons has nothing for a given line (e.g. abstract lines like "tensions were rising" won't have a period photo).
