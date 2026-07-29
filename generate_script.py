#!/usr/bin/env python3
"""
Generate script.json from a topic using Wikipedia grounding + Ollama.

Usage:  python generate_script.py "The Fall of the Berlin Wall"
Output: script.json
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

from wikipedia_fetch import fetch_wikipedia_text, fetch_eol_text

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma2:9b"
PROMPT_TEMPLATE = Path("script_prompt_template.txt")
SCRIPT_JSON = Path("script.json")

MAX_WIKI_CHARS = 4000
MIN_LINES = 6
MAX_RETRIES = 3


def call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 3500},
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
    parser.add_argument("topic", nargs="+", help="Animal name (used for Wikipedia search)")
    parser.add_argument("--title", default=None, help="Chosen title for content direction")
    args = parser.parse_args()
    topic = " ".join(args.topic)
    content_topic = args.title if args.title else topic

    print(f"\nAnimal: {topic}")
    if args.title:
        print(f"Title:  {args.title}")

    if not PROMPT_TEMPLATE.exists():
        sys.exit(f"[ERROR] {PROMPT_TEMPLATE} not found.")

    print("Fetching Wikipedia article...")
    wiki_title, wiki_text = fetch_wikipedia_text(topic)
    print(f"Found: '{wiki_title}' ({len(wiki_text)} chars)")

    print("Fetching EOL species data...")
    eol_name, eol_text = fetch_eol_text(topic)
    if eol_text:
        print(f"EOL: '{eol_name}' ({len(eol_text)} chars)")
    else:
        print("EOL: no data found, using Wikipedia only")

    reference = wiki_text[:MAX_WIKI_CHARS]
    if len(wiki_text) > MAX_WIKI_CHARS:
        reference += "\n[...truncated]"
    if eol_text:
        reference += f"\n\n[Encyclopedia of Life]\n{eol_text[:2000]}"

    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    prompt = template.replace("{{TOPIC}}", topic).replace("{{WIKIPEDIA_TEXT}}", reference)

    script = None
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            print(f"Retrying ({attempt}/{MAX_RETRIES})...")
        print(f"Calling Ollama ({OLLAMA_MODEL})...")
        raw = call_ollama(prompt)
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
    script["animal"] = topic
    SCRIPT_JSON.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    title = script.get("title", "(no title)")
    print(f"\nScript: '{title}' — {len(lines)} lines")
    for line in lines:
        print(f"  {line['id']:2d}. {line['text'][:70]}{'...' if len(line['text']) > 70 else ''}")
    print(f"\nSaved -> {SCRIPT_JSON.resolve()}")


if __name__ == "__main__":
    main()
