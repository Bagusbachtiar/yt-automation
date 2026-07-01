# YouTube Shorts Automation — Project Context

## Goal
Build an n8n automation that takes a topic idea (e.g. "cows and methane emissions") and produces a finished YouTube Short: AI-written script → text-to-speech → matched stock images → assembled video → uploaded to YouTube. Two human-in-the-loop checkpoints (script review, image review) via Telegram. Single channel/genre for now (science/educational explainer style); architecture should not hardcode this so a second channel/genre can be added later without a rewrite.

This project serves two purposes: a portfolio piece demonstrating automation/AI-integration skills for Upwork, and a potential real content pipeline if it performs well.

## Stack
- **n8n** — orchestrates the whole workflow (developer already runs n8n locally on Windows, Docker, LAN IP 192.168.100.4)
- **Ollama, model: `llama3:latest` (8B, base Llama 3, not 3.3)** — generates the script, running locally, free. Tested and validated for this specific task (see "Testing results" below) — do not assume it needs upgrading to llama3.3 unless new failures appear on more topics.
- **Kokoro TTS** (model: Kokoro-82M, Apache 2.0, self-hosted, free, commercial use allowed) — text-to-speech, not yet implemented/tested. Install: `pip install kokoro soundfile` + `espeak-ng` system dependency. Primary voice: `af_heart` (warm/natural); fallback: `af_bella` if `af_heart` doesn't sound right in practice. 54 voices across 8 languages available if we need to switch later.
- **Pexels / Pixabay API** — free stock image search, one query per script line using the `image_keyword` field
- **ffmpeg** — video assembly: image display timed to TTS audio duration per line, Ken Burns zoompan effect, caption burn-in, crossfades. NOT YET BUILT — this is the highest-risk/most-unknown part of the pipeline and should be prototyped manually (script → fake/real TTS file → folder of images picked by hand → one assembled test video) before wiring automation around it.
- **YouTube Data API v3** — final upload step with title/description/tags (default quota 10k units/day, one upload ≈ 1600 units)
- **Telegram** (n8n native node) — review/approval interface for both checkpoints, using n8n's Wait-for-webhook pattern to pause and resume execution

Later (v2, not yet started): a Next.js 16 + Tailwind v4 + Prisma + PostgreSQL (Neon) + Cloudinary web dashboard as a nicer review UI, replacing/supplementing Telegram. Matches developer's existing stack from other projects (Toko Riski / payment gateway project). Schema should be `channels` → `videos` → `script_lines` → `image_candidates` if/when built, but this is deliberately deferred until the single-channel pipeline works end to end.

## Pipeline flow
1. Topic input (manual trigger for now — "cows and methane emissions")
2. n8n → Ollama (llama3, using the tested prompt template below) → script as structured JSON
3. **Checkpoint 1**: n8n sends script to Telegram, pauses, waits for approval/edits
4. TTS generation (Kokoro TTS, `af_heart` voice) — not yet built
5. Image keyword search per line (Pexels/Pixabay API) — not yet built
6. **Checkpoint 2**: n8n sends matched image candidates to Telegram, pauses, waits for approval/swap
7. ffmpeg assembly (images + audio + captions + zoompan) — not yet built, build/test this first
8. YouTube upload via Data API v3 — not yet built

## What's already validated (script generation step only)
The prompt template below was iterated through 3 rounds of real testing against `llama3:latest` on the topic "cows and methane emissions" and now reliably produces:
- Valid, parseable JSON (no markdown fences, correct braces)
- No fabricated/contested statistics (explicitly caught and fixed: the model initially invented a "cattle = third largest emitter" claim that is a real but contested/debunked comparison in climate science circles — see rule 3)
- No cross-mechanism factual errors (initially conflated sustainable farming's fertilizer reduction with methane reduction, when fertilizer/pesticides relate to nitrous oxide, not methane from enteric fermentation — see rule 5, the fix for this)
- Image keywords that are concrete/searchable AND relevant to the specific line's content (initially produced unusable keywords like "cow robot", "giant cow", "recycle bin" — see rules 6 and 7)

**Not yet tested**: whether this template holds up on topics further from "cows and methane" (a well-worn topic in training data). Before trusting this for unattended runs, test 2-3 more varied topics and check for the same failure patterns (invented stats, mixed-up causal mechanisms, generic image keywords).

**Also not yet addressed**: the developer's example topic phrasing ("cows make more emissions than cars") is itself a contested comparative claim — the fact-checking rules govern claims *inside* the generated script, not whether the input topic itself is a loaded/oversimplified framing. Topics should be input as neutral subjects, not comparative claims to defend, or a human should sanity-check the topic itself before submission.

### Tested prompt template (script_prompt_template.txt)

```
You are a scriptwriter for short educational YouTube videos (60-90 seconds).

Write a script about: {{TOPIC}}

STRICT RULES:
1. Output ONLY valid JSON. No markdown code fences, no explanation text before or after, no trailing commas. The output must start with { and end with }.
2. Break the script into 8-12 short lines. Each line = one sentence, spoken naturally.
3. FACT-CHECKING RULE: Only include numeric or statistical claims that are well-established scientific consensus. If a claim is contested, debated, or comes from a single viral source (e.g. "if X were a country, it would rank #3"), do NOT include it. When in doubt, describe the phenomenon qualitatively instead of citing a specific number.
4. Do not invent statistics. If you are not highly confident a number is accurate, omit the number and describe the trend in words instead (e.g. "a major source of" instead of "responsible for 34% of").
5. CAUSAL PRECISION RULE: Do not attribute an effect to a cause unless the direct causal link is well-established. Do not merge two related-but-distinct phenomena into one causal sentence — for example, do not say a farming practice "reduces methane emissions" if what it actually reduces is a different gas or a different problem (fertilizer runoff, nitrous oxide, water use, etc). If a line touches on a mechanism you're unsure about, state the effect in general terms ("has a smaller environmental footprint") rather than naming a specific gas or process you're not certain applies.
6. image_keyword must be a concrete, literal, photographable noun phrase that would return real results on a stock photo site (Pexels/Pixabay). Never use abstract, humorous, or metaphorical phrases (bad: "cow robot", "giant cow", "cow flatulence" — good: "cattle farm", "dairy cow close up", "methane gas flame").
7. image_keyword must directly depict the subject of THAT specific line, not a generic theme for the video overall. If a line is about sustainable farming, the keyword should be something like "sustainable farm field" or "solar panels farm" — not a generic environmental stock phrase like "recycle" or "eco friendly" that doesn't match what the sentence is actually describing. Re-read each line and ask: would this exact keyword's photo make sense playing under this exact sentence?
8. Tone: clear, engaging, conversational — not robotic, not sensationalized.
9. End with a short, grounded closing line (no call to buy/subscribe, just a thought or takeaway).

Output format (JSON only, no other text):
{
  "title": "short catchy video title",
  "lines": [
    {"id": 1, "text": "line of narration here", "image_keyword": "concrete searchable phrase"},
    {"id": 2, "text": "...", "image_keyword": "..."}
  ]
}
```

## Explicit non-goals / decisions already made (don't relitigate these)
- **No AI-generated video** — Sora is dead (shut down April 2026), and AI-generated animals/visuals still look unconvincing anyway. Using real stock footage/images instead.
- **No clipping/repurposing other creators' content** — copyright risk ruled out earlier in project discussion. Content is either original AI-scripted narration over stock footage, or (future idea, not started) the developer's own dev-process recordings.
- **No desktop app** — web app (deferred) or Telegram (now) only, for the review interface.
- **Not using Claude API/subscription inside the automation** — decided to keep the automated daily run fully local/zero-marginal-cost via Ollama. Claude's role is limited to one-time prompt template design (already done, see template above) and ad hoc human assistance, not a per-video API call.
- **Multi-channel/multi-genre is an explicit future goal, not current scope** — build and validate one channel first.

## Known open risks (not yet solved, worth keeping in mind while building)
- **Monetization/demonetization risk**: AI-narrated, stock-image-assembled content sits in the category YouTube has tightened demonetization rules around since 2025 (mass-produced/low-effort AI content). This isn't fixed by better prompts — it's a content-format risk to watch once the channel is live.
- **ffmpeg is unbuilt and is the highest-uncertainty piece of the whole pipeline** — build and test this manually before automating around it.
- **Image relevance still isn't perfect** even after 3 prompt iterations (occasional generic-but-defensible mismatches slip through) — this is expected to always need the human review checkpoint, not something to engineer away entirely.

## Immediate next step
Build and manually test the ffmpeg assembly step: take a script (from the template above), a folder of manually-picked images, and either a real or placeholder TTS audio file, and produce one working assembled video with per-line timing, zoompan, and caption burn-in. Once that works standalone, wire n8n around it (trigger → Ollama → Telegram checkpoint → Kokoro TTS → Pexels/Pixabay → Telegram checkpoint → ffmpeg → YouTube upload).
