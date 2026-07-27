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
import re
import ssl
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

from wikipedia_fetch import fetch_wikipedia_images, is_acceptable_license, search_wikipedia

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FLICKR_API  = "https://api.flickr.com/services/rest/"
LOC_API     = "https://www.loc.gov/photos/"
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


# ── Relevance filter ──────────────────────────────────────────────────────────

_STOP = {"the", "a", "an", "of", "in", "at", "to", "for", "and", "or", "is", "was", "by"}

def _relevant_overlap(query: str, candidate: str, min_matches: int = 1) -> bool:
    terms = {w.lower() for w in re.split(r"\W+", query) if len(w) >= 4 and w.lower() not in _STOP}
    if not terms:
        return True
    c = candidate.lower()
    hits = sum(1 for t in terms if t in c)
    return hits >= min(min_matches, len(terms))


def _best_wiki_match(keyword: str, pool: list[str], used: set) -> str | None:
    """Pick pool URL whose filename best overlaps with keyword; skip already-used URLs."""
    def score(url: str) -> int:
        name = re.sub(r'^\d+px-', '', url.split("/")[-1].split("?")[0])
        name = re.sub(r'\.\w+$', '', name).replace("_", " ")
        terms = {w.lower() for w in re.split(r"\W+", keyword) if len(w) >= 4 and w.lower() not in _STOP}
        return sum(1 for t in terms if t in name.lower()) if terms else 0

    candidates = [(score(u), u) for u in pool if u not in used]
    if not candidates:
        return None
    best_score, best_url = max(candidates, key=lambda x: x[0])
    return best_url if best_score > 0 else None


# ── Wikipedia keyword search (per-line) ──────────────────────────────────────

def wikipedia_keyword_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    title = search_wikipedia(query)
    if not title or not _relevant_overlap(query, title):
        return []
    return fetch_wikipedia_images(title, limit=limit)


# ── Flickr Commons ───────────────────────────────────────────────────────────

def flickr_commons_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    params = urllib.parse.urlencode({
        "method":         "flickr.photos.search",
        "api_key":        api_key,
        "text":           query,
        "license":        "7,9,10",  # no known copyright / CC0 / public domain mark
        "extras":         "url_l,url_m",
        "per_page":       limit * 3,
        "page":           1,
        "sort":           "relevance",
        "format":         "json",
        "nojsoncallback": 1,
    })
    try:
        req = urllib.request.Request(
            f"{FLICKR_API}?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        photos = data.get("photos", {}).get("photo", [])
        urls = []
        for p in photos:
            url = p.get("url_l") or p.get("url_m")
            if url:
                urls.append(url)
                if len(urls) >= limit:
                    break
        return urls
    except Exception as e:
        print(f"    [Flickr] error: {e}")
    return []


# ── Library of Congress ───────────────────────────────────────────────────────

def loc_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    params = urllib.parse.urlencode({
        "q":  query,
        "fo": "json",
        "c":  limit * 3,
    })
    try:
        req = urllib.request.Request(
            f"{LOC_API}?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        results = data.get("results", [])
        urls = []
        for item in results:
            image_url = item.get("image_url")
            if not image_url:
                continue
            url = (image_url[-1] if isinstance(image_url, list) else image_url).split("#")[0]
            if url and any(url.lower().split("?")[0].endswith(ext) for ext in COMMONS_IMAGE_EXTS):
                urls.append(url)
                if len(urls) >= limit:
                    break
        return urls
    except Exception as e:
        print(f"    [LoC] error: {e}")
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


# ── Pexels Video ─────────────────────────────────────────────────────────────

def pexels_video_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict]:
    """Returns [{"url": video_url, "thumb": thumbnail_url}, ...]"""
    params = urllib.parse.urlencode({
        "query":       query,
        "per_page":    limit * 2,
        "page":        1,
        "orientation": "portrait",
    })
    req = urllib.request.Request(
        f"https://api.pexels.com/videos/search?{params}",
        headers={"Authorization": api_key, "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        results = []
        for v in data.get("videos", []):
            thumb = v.get("image", "")
            files = v.get("video_files", [])
            portrait = [f for f in files if f.get("width", 1) < f.get("height", 1)]
            candidates = portrait or files
            if not candidates or not thumb:
                continue
            best = max(candidates, key=lambda f: f.get("width", 0) * f.get("height", 0))
            url = best.get("link", "")
            if url:
                results.append({"url": url, "thumb": thumb})
                if len(results) >= limit:
                    break
        return results
    except urllib.error.HTTPError as e:
        print(f"    [Pexels Video] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pexels Video] error: {e}")
    return []


# ── Candidates ────────────────────────────────────────────────────────────────

def fetch_candidates(query: str, pexels_key: str, pixabay_key: str,
                     flickr_key: str = "", wiki_url: str | None = None) -> dict:
    return {
        "wikipedia":    [wiki_url] if wiki_url else [],
        "wiki_keyword": wikipedia_keyword_search(query),
        "flickr":       flickr_commons_search(query, flickr_key) if flickr_key else [],
        "commons":      commons_search(query),
        "pexels":       pexels_search(query, pexels_key)         if pexels_key else [],
        "pixabay":      pixabay_search(query, pixabay_key)       if pixabay_key else [],
        "pexels_video": pexels_video_search(query, pexels_key)   if pexels_key else [],
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
    flickr_key  = os.environ.get("FLICKR_API_KEY",  "").strip()
    tg_token    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not pexels_key and not pixabay_key and not flickr_key:
        sys.exit("[ERROR] No API keys. Set PEXELS_API_KEY, PIXABAY_API_KEY, or FLICKR_API_KEY in .env")
    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    print(f"\nCollecting candidates for {len(lines)} lines...\n")

    wiki_pool = []
    wiki_title = script.get("wiki_title", "")
    if wiki_title:
        print(f"Fetching Wikipedia article images for '{wiki_title}'...")
        wiki_pool = fetch_wikipedia_images(wiki_title)
        print(f"  {len(wiki_pool)} licensed images in pool\n")

    IMAGES_DIR.mkdir(exist_ok=True)
    all_candidates = {}
    used_wiki = set()

    for idx, line in enumerate(lines):
        lid = line["id"]
        keyword = (
            line.get("image_keyword")
            or (line.get("image_keywords") or [None])[0]
            or line["text"]
        )
        fallback_keyword = line.get("image_keyword_fallback", "")
        wiki_url = None
        if wiki_pool:
            wiki_url = _best_wiki_match(keyword, wiki_pool, used_wiki)
            if wiki_url:
                used_wiki.add(wiki_url)
        print(f"  Line {lid:2d}: {keyword}")
        c = fetch_candidates(keyword, pexels_key, pixabay_key, flickr_key=flickr_key, wiki_url=wiki_url)
        if not c["wiki_keyword"] and fallback_keyword and fallback_keyword != keyword:
            print(f"    [fallback] no wiki match — trying: {fallback_keyword}")
            c["wiki_keyword"] = wikipedia_keyword_search(fallback_keyword)
        total = sum(len(v) for v in c.values())
        print(f"    wiki:{len(c['wikipedia'])}  wiki_kw:{len(c['wiki_keyword'])}  flickr:{len(c['flickr'])}  commons:{len(c['commons'])}  pexels:{len(c['pexels'])}  pixabay:{len(c['pixabay'])}  pexels_vid:{len(c['pexels_video'])}  total:{total}")
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
            url = (sources["wikipedia"] or sources["wiki_keyword"] or sources["flickr"] or sources["commons"] or sources["pexels"] or sources["pixabay"] or [None])[0]
            if not url:
                print(f"  Line {lid_str}: no result from any source")
                failed += 1
                continue
            try:
                download(url, dest)
                for src in ("wikipedia", "wiki_keyword", "flickr", "commons", "pexels", "pixabay"):
                    if url in sources.get(src, []):
                        break
                print(f"  {lid_str}.jpg  ({src}, {dest.stat().st_size // 1024} KB)")
                ok += 1
            except Exception as e:
                print(f"  Line {lid_str}: download failed — {e}")
                failed += 1

        print(f"\nDone. OK={ok}  failed={failed}")
        print("Run: python generate_audio.py && python assemble.py")


if __name__ == "__main__":
    main()
