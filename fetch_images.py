#!/usr/bin/env python3
"""
Fetch image candidates per script line (top 3 per source).

Saves image_candidates.json with URLs from Commons / Pexels / Pixabay.

If TELEGRAM_BOT_TOKEN is set in .env:  run review_images.py next to pick images.
If not:                                 auto-downloads first result per line to images/.

Run:  python fetch_images.py
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

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_IMAGE_EXTS = (".jpg", ".jpeg", ".png")
CANDIDATES_PER_SOURCE = 3

SCRIPT_JSON      = Path("script.json")
IMAGES_DIR       = Path("images")
CANDIDATES_JSON  = Path("image_candidates.json")

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


# ── Wikimedia Commons ─────────────────────────────────────────────────────────

def is_acceptable_license(license_str: str) -> bool:
    l = license_str.lower().strip()
    if not l:
        return False
    if "public domain" in l or l == "cc0":
        return True
    if l.startswith("cc by") and "sa" not in l and "nc" not in l and "nd" not in l:
        return True
    return False


def commons_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    try:
        params = urllib.parse.urlencode({
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": limit * 3,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1080,
            "iiextmetadatafilter": "LicenseShortName",
            "format": "json",
        })
        req = urllib.request.Request(
            f"{COMMONS_API}?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        pages = data.get("query", {}).get("pages", {})
        urls = []
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            license_str = (info.get("extmetadata", {})
                               .get("LicenseShortName", {})
                               .get("value", ""))
            img_url = info.get("thumburl") or info.get("url", "")
            if not img_url:
                continue
            if not any(img_url.lower().split("?")[0].endswith(ext) for ext in COMMONS_IMAGE_EXTS):
                continue
            if is_acceptable_license(license_str):
                urls.append(img_url)
                if len(urls) >= limit:
                    break
        return urls
    except Exception as e:
        print(f"    [Commons] error: {e}")
    return []


# ── Pexels ────────────────────────────────────────────────────────────────────

def pexels_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    params = urllib.parse.urlencode({
        "query":       query,
        "orientation": PEXELS_ORIENTATION,
        "per_page":    limit * 2,
        "page":        1,
    })
    req = urllib.request.Request(
        f"https://api.pexels.com/v1/search?{params}",
        headers={
            "Authorization": api_key,
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        return [p["src"]["large2x"] for p in data.get("photos", [])[:limit]]
    except urllib.error.HTTPError as e:
        print(f"    [Pexels] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pexels] error: {e}")
    return []


# ── Pixabay ───────────────────────────────────────────────────────────────────

def pixabay_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    params = urllib.parse.urlencode({
        "key":         api_key,
        "q":           query,
        "image_type":  "photo",
        "orientation": PIXABAY_ORIENTATION,
        "per_page":    limit * 2,
        "page":        1,
        "safesearch":  "true",
    })
    try:
        with urllib.request.urlopen(
            f"https://pixabay.com/api/?{params}", timeout=10, context=_SSL_CTX
        ) as resp:
            data = json.loads(resp.read())
        return [h["largeImageURL"] for h in data.get("hits", [])[:limit] if h.get("largeImageURL")]
    except urllib.error.HTTPError as e:
        print(f"    [Pixabay] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pixabay] error: {e}")
    return []


# ── Candidates ────────────────────────────────────────────────────────────────

def fetch_candidates(query: str, pexels_key: str, pixabay_key: str) -> dict:
    return {
        "commons": commons_search(query),
        "pexels":  pexels_search(query, pexels_key)  if pexels_key  else [],
        "pixabay": pixabay_search(query, pixabay_key) if pixabay_key else [],
    }


# ── Download ──────────────────────────────────────────────────────────────────

def download(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "yt-automation/1.0"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                dest.write_bytes(resp.read())
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                print(f"    [rate limit] waiting 5s...")
                time.sleep(5)
            else:
                raise


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_env()
    pexels_key  = os.environ.get("PEXELS_API_KEY",  "").strip()
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    tg_token    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not pexels_key and not pixabay_key:
        sys.exit("[ERROR] No API keys. Set PEXELS_API_KEY and/or PIXABAY_API_KEY in .env")
    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    print(f"\nCollecting candidates for {len(lines)} lines...\n")

    IMAGES_DIR.mkdir(exist_ok=True)
    all_candidates = {}

    for line in lines:
        lid = line["id"]
        keyword = (
            line.get("image_keyword")
            or (line.get("image_keywords") or [None])[0]
            or line["text"]
        )
        print(f"  Line {lid:2d}: {keyword}")
        c = fetch_candidates(keyword, pexels_key, pixabay_key)
        total = len(c["commons"]) + len(c["pexels"]) + len(c["pixabay"])
        print(f"    commons:{len(c['commons'])}  pexels:{len(c['pexels'])}  pixabay:{len(c['pixabay'])}  total:{total}")
        all_candidates[str(lid)] = {
            "text":    line["text"],
            "keyword": keyword,
            "sources": c,
        }
        time.sleep(SLEEP_BETWEEN)

    CANDIDATES_JSON.write_text(
        json.dumps(all_candidates, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSaved -> {CANDIDATES_JSON}")

    if tg_token:
        print("Run: python review_images.py")
    else:
        # No Telegram — auto-pick first result per line
        print("\nNo TELEGRAM_BOT_TOKEN — auto-picking first result per line...")
        ok = failed = 0
        for lid_str, data in all_candidates.items():
            dest = IMAGES_DIR / f"{lid_str}.jpg"
            if dest.exists():
                print(f"  {lid_str}.jpg already exists, skip")
                continue
            sources = data["sources"]
            url = (sources["commons"] or sources["pexels"] or sources["pixabay"] or [None])[0]
            if not url:
                print(f"  Line {lid_str}: no result from any source")
                failed += 1
                continue
            try:
                download(url, dest)
                src = "commons" if url in sources["commons"] else ("pexels" if url in sources["pexels"] else "pixabay")
                print(f"  {lid_str}.jpg  ({src}, {dest.stat().st_size // 1024} KB)")
                ok += 1
            except Exception as e:
                print(f"  Line {lid_str}: download failed — {e}")
                failed += 1

        print(f"\nDone. OK={ok}  failed={failed}")
        print("Run: python generate_audio.py && python assemble.py")


if __name__ == "__main__":
    main()
