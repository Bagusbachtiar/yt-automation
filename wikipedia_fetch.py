#!/usr/bin/env python3
"""
Fetch Wikipedia article summary for a given topic using fuzzy search.
No API key required.

Usage:  python wikipedia_fetch.py "The Fall of the Berlin Wall"
Import: from wikipedia_fetch import fetch_wikipedia_text
"""

import json
import ssl
import sys
import urllib.request
import urllib.parse

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "yt-automation/1.0 (bagusbachtiar50@gmail.com)"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
        return r.read()


def search_wikipedia(topic: str) -> str | None:
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": topic,
        "srlimit": 1,
        "format": "json",
    })
    data = json.loads(_get(f"{WIKI_API}?{params}"))
    results = data.get("query", {}).get("search", [])
    return results[0]["title"] if results else None


def fetch_article_text(title: str) -> str:
    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "extracts",
        "explaintext": True,
        "titles": title,
        "format": "json",
    })
    data = json.loads(_get(f"{WIKI_API}?{params}"))
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()))
    return page.get("extract", "")


def fetch_wikipedia_text(topic: str) -> tuple[str, str]:
    """Returns (matched_title, article_text). Exits on failure."""
    title = search_wikipedia(topic)
    if not title:
        sys.exit(f"[ERROR] No Wikipedia article found for: {topic}")
    text = fetch_article_text(title)
    if not text:
        sys.exit(f"[ERROR] Wikipedia article '{title}' has no extractable text.")
    return title, text


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python wikipedia_fetch.py "topic here"')
    topic = " ".join(sys.argv[1:])
    title, text = fetch_wikipedia_text(topic)
    print(f"Article: {title}\n")
    print(text[:2000])
    print(f"\n... ({len(text)} total chars)")
