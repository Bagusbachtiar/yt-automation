#!/usr/bin/env python3
"""
Generate script.json from a topic using Wikipedia grounding + Ollama.

Usage:  python generate_script.py "The Fall of the Berlin Wall"
Output: script.json
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

from wikipedia_fetch import fetch_wikipedia_text

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
    if len(sys.argv) < 2:
        sys.exit('Usage: python generate_script.py "topic here"')

    topic = " ".join(sys.argv[1:])
    print(f"\nTopic: {topic}")

    if not PROMPT_TEMPLATE.exists():
        sys.exit(f"[ERROR] {PROMPT_TEMPLATE} not found.")

    print("Fetching Wikipedia article...")
    wiki_title, wiki_text = fetch_wikipedia_text(topic)
    print(f"Found: '{wiki_title}' ({len(wiki_text)} chars)")

    # Trim to avoid blowing out the context window
    reference = wiki_text[:MAX_WIKI_CHARS]
    if len(wiki_text) > MAX_WIKI_CHARS:
        reference += "\n[...truncated]"

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

    script["wiki_title"] = wiki_title
    SCRIPT_JSON.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    title = script.get("title", "(no title)")
    print(f"\nScript: '{title}' — {len(lines)} lines")
    for line in lines:
        print(f"  {line['id']:2d}. {line['text'][:70]}{'...' if len(line['text']) > 70 else ''}")
    print(f"\nSaved -> {SCRIPT_JSON.resolve()}")


if __name__ == "__main__":
    main()
