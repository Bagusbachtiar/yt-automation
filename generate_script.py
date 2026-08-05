#!/usr/bin/env python3
"""
Generate script.json from a topic using Wikipedia grounding + Ollama.

Usage:  python generate_script.py "The Fall of the Berlin Wall"
Output: script.json
"""

import argparse
import json
import re
import ssl
import sys
import urllib.request
import urllib.parse
from pathlib import Path

from wikipedia_fetch import fetch_wikipedia_text, fetch_eol_text, fetch_gbif_species_text

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma2:9b"
PROMPT_TEMPLATE          = Path("script_prompt_template.txt")
PROMPT_TEMPLATE_SCIENCE  = Path("script_prompt_template_science.txt")
SCRIPT_JSON = Path("script.json")

MAX_WIKI_CHARS = 4000
MIN_LINES = 6
MAX_RETRIES = 3

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_SPACE_WORDS = {
    "saturn","jupiter","mars","moon","sun","solar","galaxy","nebula","black hole",
    "asteroid","comet","planet","orbit","nasa","space","star","cosmos","universe",
    "exoplanet","quasar","supernova","neutron star","dark matter","dark energy",
    "big bang","milky way","telescope","rings","aurora","meteor","pulsar",
}
_BOTANY_WORDS = {
    "plant","flower","tree","seed","pollen","root","leaf","leaves","fungi","mushroom",
    "photosynthesis","vine","moss","fern","algae","chlorophyll","carnivorous",
    "venus","flytrap","pitcher","succulent","cactus","lichen","spore","bark","sap",
}


def classify_topic(topic: str) -> str:
    """Returns 'space', 'botany', or 'general'."""
    t = topic.lower()
    if any(w in t for w in _SPACE_WORDS):
        return "space"
    if any(w in t for w in _BOTANY_WORDS):
        return "botany"
    return "general"


def _strip_question(topic: str) -> str:
    t = re.sub(r'^(why|how|what|when|where|is|does|can|do|did|will)\s+', '', topic.lower()).strip()
    t = re.sub(r'\b(has|have|are|is|was|were|exist|gets?|makes?)\b', '', t)
    return ' '.join(t.split())


def fetch_nasa_grounding(topic: str) -> str:
    """Fetch image descriptions from NASA Images API as grounding text."""
    params = urllib.parse.urlencode({"q": _strip_question(topic), "media_type": "image", "page_size": 8})
    req = urllib.request.Request(
        f"https://images-api.nasa.gov/search?{params}",
        headers={"User-Agent": "yt-automation/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        items = data.get("collection", {}).get("items", [])
        texts = []
        for item in items[:6]:
            for d in item.get("data", []):
                desc = d.get("description", "").strip()
                title = d.get("title", "").strip()
                if desc:
                    texts.append(f"{title}: {desc[:500]}" if title else desc[:500])
        return "\n\n".join(texts)
    except Exception as e:
        print(f"[WARN] NASA grounding fetch failed: {e}")
        return ""


def fetch_perenual_grounding(topic: str) -> str:
    """Fetch plant data from Perenual API if key present in .env."""
    env = Path(".env")
    key = ""
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PERENUAL_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        return ""
    params = urllib.parse.urlencode({"key": key, "q": topic, "page": 1})
    req = urllib.request.Request(
        f"https://perenual.com/api/species-list?{params}",
        headers={"User-Agent": "yt-automation/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        items = data.get("data", [])[:3]
        texts = []
        for item in items:
            name = item.get("common_name", "")
            sci  = item.get("scientific_name", [""])[0]
            desc = item.get("description", "")
            if desc:
                texts.append(f"{name} ({sci}): {desc[:600]}")
        return "\n\n".join(texts)
    except Exception as e:
        print(f"[WARN] Perenual fetch failed: {e}")
        return ""


def call_ollama(prompt: str, num_predict: int = 3500) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data.get("response", "")
    except Exception as e:
        sys.exit(f"[ERROR] Ollama call failed: {e}")


def extract_json(text: str) -> dict:
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        sys.exit(f"[ERROR] No JSON found in Ollama response:\n{text[:600]}")
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        sys.exit(f"[ERROR] JSON parse failed: {e}\nRaw:\n{m.group()[:600]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="+", help="Topic (animal name or science question)")
    parser.add_argument("--title",   default=None, help="Chosen title for content direction")
    parser.add_argument("--channel", default="faunaworks", help="Channel key (faunaworks|science)")
    args = parser.parse_args()
    topic         = " ".join(args.topic)
    content_topic = args.title if args.title else topic
    channel       = args.channel

    print(f"\nTopic:   {topic}")
    print(f"Channel: {channel}")
    if args.title:
        print(f"Title:   {args.title}")

    is_science = channel == "science"
    template_path = PROMPT_TEMPLATE_SCIENCE if is_science else PROMPT_TEMPLATE
    if not template_path.exists():
        sys.exit(f"[ERROR] {template_path} not found.")

    # ── Grounding ─────────────────────────────────────────────────────────────
    print("Fetching Wikipedia article...")
    wiki_title, wiki_text = fetch_wikipedia_text(topic)
    print(f"Found: '{wiki_title}' ({len(wiki_text)} chars)")

    # Guard: fuzzy search can return wrong articles ("How stars are born" → crime film).
    # Only use grounding if at least one non-trivial topic word appears in the article title.
    _WIKI_STOP = {"what","when","where","does","have","will","been","they","this","that",
                  "from","with","into","some","born","form","forms","formed","make","made",
                  "happen","happens","work","works","exist","need","needs","live","lives",
                  "grow","grows","cause","causes","found","feel","move","turn","keep","come",
                  "came","goes","look","seem","show","take","much","more","most","also","only",
                  "just","even","well","actually","really","truly","actually","never","always"}
    _topic_words = {w for w in topic.lower().split() if len(w) > 3 and w not in _WIKI_STOP}
    _wiki_words  = set(wiki_title.lower().split())
    # prefix match handles plurals: "rainbows"/"rainbow", "earthquakes"/"earthquake"
    _matched = any(tw.startswith(ww) or ww.startswith(tw) for tw in _topic_words for ww in _wiki_words)
    if not _matched:
        print(f"  [WARN] Wikipedia title '{wiki_title}' doesn't match topic — skipping grounding")
        wiki_text = ""

    reference = wiki_text[:MAX_WIKI_CHARS]
    if len(wiki_text) > MAX_WIKI_CHARS:
        reference += "\n[...truncated]"

    if is_science:
        topic_type = classify_topic(topic)
        print(f"Topic type: {topic_type}")
        if topic_type == "space":
            print("Fetching NASA image descriptions...")
            nasa_text = fetch_nasa_grounding(topic)
            if nasa_text:
                print(f"NASA: {len(nasa_text)} chars")
                reference += f"\n\n[NASA Image Archive]\n{nasa_text[:2000]}"
            else:
                print("NASA: no descriptions found")
        elif topic_type == "botany":
            print("Fetching Perenual plant data...")
            perenual_text = fetch_perenual_grounding(topic)
            if perenual_text:
                print(f"Perenual: {len(perenual_text)} chars")
                reference += f"\n\n[Perenual Plant Database]\n{perenual_text[:2000]}"
            else:
                print("Perenual: no data (key missing or not found)")
    else:
        print("Fetching EOL species data...")
        eol_name, eol_text = fetch_eol_text(topic)
        if eol_text:
            print(f"EOL: '{eol_name}' ({len(eol_text)} chars)")
            reference += f"\n\n[Encyclopedia of Life]\n{eol_text[:2000]}"
        else:
            print("EOL: no data found")

        print("Fetching GBIF species descriptions...")
        gbif_name, gbif_text = fetch_gbif_species_text(topic, wiki_title=wiki_title)
        if gbif_text:
            print(f"GBIF: '{gbif_name}' ({len(gbif_text)} chars)")
            reference += f"\n\n[GBIF Species Descriptions]\n{gbif_text[:2000]}"
        else:
            print("GBIF: no descriptions found")

    template = template_path.read_text(encoding="utf-8")
    direction_hint = ""
    if args.title:
        subject_word = "topic" if is_science else "animal"
        direction_hint = (
            f"\nCONTENT DIRECTION: The title for this video has already been chosen: \"{content_topic}\". "
            f"Every line in your script MUST support and be consistent with what this title promises. "
            f"Do not cover unrelated aspects of this {subject_word} — stay on the specific angle this title sets up."
        )
    prompt = (template
              .replace("{{TOPIC}}", topic)
              .replace("{{TITLE_DIRECTION}}", direction_hint)
              .replace("{{WIKIPEDIA_TEXT}}", reference))

    script = None
    active_prompt = prompt
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            print(f"Retrying ({attempt}/{MAX_RETRIES})...")
        print(f"Calling Ollama ({OLLAMA_MODEL})...")
        raw = call_ollama(active_prompt, num_predict=5000 if is_science else 3500)
        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if not m:
                print(f"  [WARN] No JSON found, retrying...")
                continue
            cleaned = re.sub(r',(\s*[}\]])', r'\1', m.group())
            candidate = json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"  [WARN] JSON parse failed: {e}, retrying...")
            continue
        lines = candidate.get("lines", [])
        if len(lines) < MIN_LINES:
            print(f"  [WARN] Only {len(lines)} lines (need {MIN_LINES}+), retrying...")
            active_prompt = (prompt +
                f"\n\nCRITICAL REMINDER: Your last response only had {len(lines)} lines. "
                f"You MUST write at least {MIN_LINES} lines. "
                f"Add more interesting facts, mechanisms, and examples about this topic to reach the required count.")
            continue
        script = candidate
        break

    if not script:
        sys.exit(f"[ERROR] Failed to get a valid script with {MIN_LINES}+ lines after {MAX_RETRIES} attempts.")

    lines = script.get("lines", [])

    # strip em dashes the model still sneaks in despite the prompt rule
    for ln in lines:
        ln["text"] = ln["text"].replace("—", ",").replace(" ,", ",")
    if "title" in script:
        script["title"] = script["title"].replace("—", ",").replace(" ,", ",")

    script["wiki_title"] = wiki_title
    if is_science:
        script["topic"] = topic
    else:
        script["animal"] = topic
    SCRIPT_JSON.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    title = script.get("title", "(no title)")
    print(f"\nScript: '{title}' — {len(lines)} lines")
    for line in lines:
        print(f"  {line['id']:2d}. {line['text'][:70]}{'...' if len(line['text']) > 70 else ''}")
    print(f"\nSaved -> {SCRIPT_JSON.resolve()}")


if __name__ == "__main__":
    main()
