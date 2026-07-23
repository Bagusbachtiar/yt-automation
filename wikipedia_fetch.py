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

WIKI_API    = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT  = "yt-automation/1.0 (bagusbachtiar50@gmail.com)"
_IMG_EXTS   = (".jpg", ".jpeg", ".png")

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


def is_acceptable_license(license_str: str) -> bool:
    l = license_str.lower().strip()
    if not l:
        return False
    if "public domain" in l or l == "cc0":
        return True
    if l.startswith("cc by") and "sa" not in l and "nc" not in l and "nd" not in l:
        return True
    return False


def fetch_wikipedia_images(title: str, limit: int = 30) -> list[str]:
    """Return up to limit image URLs from the Wikipedia article (public domain/CC0/CC-BY only)."""
    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "images",
        "titles": title,
        "imlimit": 50,
        "format": "json",
    })
    data = json.loads(_get(f"{WIKI_API}?{params}"))
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()))
    filenames = [
        img["title"] for img in page.get("images", [])
        if any(img["title"].lower().endswith(ext) for ext in _IMG_EXTS)
    ]
    if not filenames:
        return []

    results = []
    for i in range(0, len(filenames), 50):
        batch = filenames[i:i + 50]
        params = urllib.parse.urlencode({
            "action": "query",
            "titles": "|".join(batch),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1080,
            "iiextmetadatafilter": "LicenseShortName",
            "format": "json",
        })
        data = json.loads(_get(f"{COMMONS_API}?{params}"))
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            license_str = (info.get("extmetadata", {})
                               .get("LicenseShortName", {})
                               .get("value", ""))
            if not is_acceptable_license(license_str):
                continue
            img_url = info.get("thumburl") or info.get("url", "")
            if not img_url:
                continue
            if any(img_url.lower().split("?")[0].endswith(ext) for ext in _IMG_EXTS):
                results.append(img_url)
                if len(results) >= limit:
                    return results
    return results


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
