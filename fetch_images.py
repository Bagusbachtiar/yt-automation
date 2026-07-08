#!/usr/bin/env python3
"""
Fetch images per script line from Pexels (primary) or Pixabay (fallback).
Supports multiple images per line via image_keywords array in script.json.

Output: images/1_1.jpg, images/1_2.jpg, images/2_1.jpg, ...
Run:    python fetch_images.py
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# Windows Python SSL bundle often has expired certs — bypass verification
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

SCRIPT_JSON = Path("script.json")
IMAGES_DIR  = Path("images")

PEXELS_ORIENTATION  = "portrait"
PIXABAY_ORIENTATION = "vertical"

SLEEP_BETWEEN = 0.3


# ── Env loader ────────────────────────────────────────────────────────────────

def load_env():
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# ── Pexels ────────────────────────────────────────────────────────────────────

def pexels_search(query: str, api_key: str) -> str | None:
    params = urllib.parse.urlencode({
        "query":       query,
        "orientation": PEXELS_ORIENTATION,
        "per_page":    5,
        "page":        1,
    })
    url = f"https://api.pexels.com/v1/search?{params}"
    req = urllib.request.Request(url, headers={
        "Authorization": api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    })
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        photos = data.get("photos", [])
        if not photos:
            return None
        return photos[0]["src"]["large2x"]
    except urllib.error.HTTPError as e:
        print(f"    [Pexels] HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"    [Pexels] error: {e}")
        return None


# ── Pixabay ───────────────────────────────────────────────────────────────────

def pixabay_search(query: str, api_key: str) -> str | None:
    params = urllib.parse.urlencode({
        "key":         api_key,
        "q":           query,
        "image_type":  "photo",
        "orientation": PIXABAY_ORIENTATION,
        "per_page":    5,
        "page":        1,
        "safesearch":  "true",
    })
    url = f"https://pixabay.com/api/?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        hits = data.get("hits", [])
        if not hits:
            return None
        return hits[0].get("largeImageURL")
    except urllib.error.HTTPError as e:
        print(f"    [Pixabay] HTTP {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"    [Pixabay] error: {e}")
        return None


# ── Download ──────────────────────────────────────────────────────────────────

def fetch_one(query: str, pexels_key: str, pixabay_key: str) -> str | None:
    img_url = None
    if pexels_key:
        img_url = pexels_search(query, pexels_key)
        if img_url:
            print(f"      → Pexels")
    if not img_url and pixabay_key:
        img_url = pixabay_search(query, pixabay_key)
        if img_url:
            print(f"      → Pixabay")
    return img_url


def download(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-automation/1.0"})
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        dest.write_bytes(resp.read())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_env()
    pexels_key  = os.environ.get("PEXELS_API_KEY",  "").strip()
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()

    if not pexels_key and not pixabay_key:
        sys.exit("[ERROR] No API keys found. Set PEXELS_API_KEY and/or PIXABAY_API_KEY in .env")

    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    print(f"\nFetching images for {len(lines)} lines\n")

    IMAGES_DIR.mkdir(exist_ok=True)

    ok = skipped = failed = 0

    for line in lines:
        lid = line["id"]
        keyword = (
            line.get("image_keyword")
            or (line.get("image_keywords") or [None])[0]
            or line["text"]
        )
        dest = IMAGES_DIR / f"{lid}.jpg"

        print(f"  Line {lid:2d}: {keyword}")

        if dest.exists():
            print(f"    already exists — skip")
            skipped += 1
            continue

        img_url = fetch_one(keyword, pexels_key, pixabay_key)

        if not img_url:
            print(f"    [FAIL] no result from any provider")
            failed += 1
            time.sleep(SLEEP_BETWEEN)
            continue

        try:
            download(img_url, dest)
            size_kb = dest.stat().st_size // 1024
            print(f"    saved {dest.name} ({size_kb} KB)")
            ok += 1
        except Exception as e:
            print(f"    [FAIL] download error: {e}")
            failed += 1

        time.sleep(SLEEP_BETWEEN)

    print(f"\nDone. OK={ok}  skipped={skipped}  failed={failed}")
    if ok + skipped > 0:
        print(f"Run: python generate_audio.py && python assemble.py")


if __name__ == "__main__":
    main()
