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

from wikipedia_fetch import (fetch_wikipedia_images, fetch_wikipedia_pageimage,
                             is_acceptable_license, search_wikipedia)

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

# Food/commercial context suppression — applied to Pexels alt text and Pixabay tags.
# Catches food-fish problem: tuna/salmon results dominated by sushi plates and market counters.
# Only applied when the image keyword and script line text don't signal a fishing/industry topic
# (so lines about overfishing can still surface fishing boat imagery).
_FOOD_SIGNALS = frozenset({
    # Food preparation / serving
    "sushi", "sashimi", "fillet", "filet",
    "seafood", "fishmonger",
    "grilled", "smoked", "fried", "baked", "cooked",
    "plate", "dish", "meal", "recipe",
    "canned", "grocery", "supermarket", "restaurant",
    "cutting board",
    # Raw / dead food context ("raw" alone risks false positives like "raw footage")
    "raw fish", "fresh catch",
    # Commercial / market
    "fish market", "market stall", "seafood market",
    # Industrial fishing — excluded unless line is about overfishing/conservation
    "fishing boat", "fishing net", "fishing vessel", "fish haul",
})
_FISHING_QUERY_SIGNALS = frozenset({
    "fishing", "fisherman", "overfishing", "bycatch",
    "trawl", "trawler", "commercial",
})
_FISHING_LINE_SIGNALS  = frozenset({"overfishing", "bycatch", "trawl", "fishing industry"})

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

def pexels_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE,
                  require_terms: list[str] | None = None,
                  exclude_terms: frozenset | None = None) -> list[str]:
    params = urllib.parse.urlencode({
        "query":       query,
        "orientation": PEXELS_ORIENTATION,
        "per_page":    limit * 4,
        "page":        1,
    })
    req = urllib.request.Request(
        f"https://api.pexels.com/v1/search?{params}",
        headers={"Authorization": api_key, "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        photos  = data.get("photos", [])
        incl    = require_terms or []
        excl    = exclude_terms or frozenset()
        matched = [
            p["src"]["large2x"] for p in photos
            if (not incl or all(t in p.get("alt", "").lower() for t in incl))
            and (not excl or not any(e in p.get("alt", "").lower() for e in excl))
        ][:limit]
        return matched
    except urllib.error.HTTPError as e:
        print(f"    [Pexels] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pexels] error: {e}")
    return []


# ── Pixabay ───────────────────────────────────────────────────────────────────

def pixabay_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE,
                   require_terms: list[str] | None = None,
                   exclude_terms: frozenset | None = None) -> list[str]:
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
        hits  = data.get("hits", [])
        incl  = require_terms or []
        excl  = exclude_terms or frozenset()
        matched = [
            h["largeImageURL"] for h in hits
            if h.get("largeImageURL")
            and (not incl or all(t in h.get("tags", "").lower() for t in incl))
            and (not excl or not any(e in h.get("tags", "").lower() for e in excl))
        ][:limit]
        return matched
    except urllib.error.HTTPError as e:
        print(f"    [Pixabay] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pixabay] error: {e}")
    return []


# ── iNaturalist ───────────────────────────────────────────────────────────────

def inaturalist_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    """Community observation photos (CC-licensed). Biodiversity DB — search relevance is intrinsic."""
    params = urllib.parse.urlencode({
        "q":        query,
        "photos":   "true",
        "per_page": limit * 4,
        "order_by": "votes",
        "license":  "cc0,cc-by,cc-by-sa,cc-by-nc,cc-by-nc-sa",
    })
    try:
        req = urllib.request.Request(
            f"https://api.inaturalist.org/v1/observations?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        urls = []
        for obs in data.get("results", []):
            for photo in obs.get("photos", []):
                url = photo.get("url", "").replace("/square.", "/large.")
                if url:
                    urls.append(url)
                    if len(urls) >= limit:
                        return urls
        return urls
    except Exception as e:
        print(f"    [iNaturalist] error: {e}")
    return []


# ── GBIF ──────────────────────────────────────────────────────────────────────

def gbif_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    """Species occurrence images from GBIF (no key, free). Biodiversity DB — search relevance is intrinsic."""
    params = urllib.parse.urlencode({
        "q":         query,
        "mediaType": "StillImage",
        "limit":     limit * 4,
    })
    try:
        req = urllib.request.Request(
            f"https://api.gbif.org/v1/occurrence/search?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        urls = []
        for occ in data.get("results", []):
            for media in occ.get("media", []):
                if media.get("type") != "StillImage":
                    continue
                url = media.get("identifier", "")
                lic = media.get("license", "").lower()
                if url and ("creativecommons" in lic or not lic):
                    urls.append(url)
                    if len(urls) >= limit:
                        return urls
        return urls
    except Exception as e:
        print(f"    [GBIF] error: {e}")
    return []


# ── Pexels Video ─────────────────────────────────────────────────────────────

def pexels_video_search(query: str, api_key: str, limit: int = CANDIDATES_PER_SOURCE,
                        require_terms: list[str] | None = None,
                        exclude_terms: frozenset | None = None) -> list[dict]:
    """Returns [{"url": video_url, "thumb": thumbnail_url}, ...]"""
    params = urllib.parse.urlencode({
        "query":       query,
        "per_page":    limit * 4,
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
        incl = require_terms or []
        excl = exclude_terms or frozenset()

        def _parse(videos):
            results = []
            for v in videos:
                thumb = v.get("image", "")
                # Thumbnail filename has descriptive tags (e.g. "red-panda-wildlife-19477927.jpeg")
                # Page URL slug often has only photographer name — check both for terms
                thumb_name = thumb.split("/")[-1].split("?")[0].lower()
                page_url   = v.get("url", "").lower()
                text       = page_url + " " + thumb_name
                files = v.get("video_files", [])
                portrait = [f for f in files if f.get("width", 1) < f.get("height", 1)]
                candidates = portrait or files
                if not candidates or not thumb:
                    continue
                best = max(candidates, key=lambda f: f.get("width", 0) * f.get("height", 0))
                url = best.get("link", "")
                if url:
                    results.append({"url": url, "thumb": thumb, "_text": text})
            return results

        all_parsed = _parse(data.get("videos", []))
        matched = [
            r for r in all_parsed
            if (not incl or all(t in r["_text"] for t in incl))
            and (not excl or not any(e in r["_text"] for e in excl))
        ][:limit]
        return [{"url": r["url"], "thumb": r["thumb"]} for r in matched]
    except urllib.error.HTTPError as e:
        print(f"    [Pexels Video] HTTP {e.code}: {e.reason}")
    except Exception as e:
        print(f"    [Pexels Video] error: {e}")
    return []


# ── Candidates ────────────────────────────────────────────────────────────────

def _shorten(query: str) -> list[str]:
    """Return progressively shorter keyword variants to try as fallbacks."""
    words = query.split()
    variants = [query]
    if len(words) > 2:
        variants.append(" ".join(words[:2]))
    if len(words) > 1:
        variants.append(words[0])
    return list(dict.fromkeys(variants))  # dedupe, preserve order


def _pexels_with_fallback(query: str, api_key: str, fn, require_terms: list[str],
                          exclude_terms: frozenset | None = None,
                          limit: int = CANDIDATES_PER_SOURCE) -> list:
    for kw in _shorten(query):
        results = fn(kw, api_key, limit=limit, require_terms=require_terms,
                     exclude_terms=exclude_terms)
        if results:
            if kw != query:
                print(f"    [fallback keyword] '{query}' -> '{kw}'")
            return results
    return []


def fetch_candidates(query: str, pexels_key: str, pixabay_key: str,
                     flickr_key: str = "", wiki_url: str | None = None,
                     animal: str = "", line_text: str = "") -> dict:
    # 1-word: check that word; 2-word: require both ("sea pig" needs "sea" AND "pig");
    # 3+-word: last word only (modifier words like "great" are too generic to require)
    words = animal.lower().split() if animal else []
    if len(words) <= 2:
        require_terms = words
    else:
        require_terms = [words[-1]]

    # Suppress food/commercial imagery (sushi, fillet, plate…) unless the keyword or
    # line text signals that fishing/industry context is intentional (overfishing lines,
    # conservation lines — those should be able to surface boat/net imagery).
    fishing_context = bool(set(query.lower().split()) & _FISHING_QUERY_SIGNALS) or \
                      any(t in line_text.lower() for t in _FISHING_LINE_SIGNALS)
    exclude_terms = frozenset() if fishing_context else _FOOD_SIGNALS

    return {
        "wikipedia":    [wiki_url] if wiki_url else [],
        "wiki_keyword": wikipedia_keyword_search(query),
        "flickr":       flickr_commons_search(query, flickr_key) if flickr_key else [],
        "commons":      commons_search(query),
        "pexels":       _pexels_with_fallback(query, pexels_key, pexels_search, require_terms, exclude_terms)       if pexels_key else [],
        "pixabay":      pixabay_search(query, pixabay_key, require_terms=require_terms, exclude_terms=exclude_terms) if pixabay_key else [],
        "inaturalist":  inaturalist_search(query),
        "gbif":         gbif_search(query),
        "pexels_video": _pexels_with_fallback(query, pexels_key, pexels_video_search, require_terms, exclude_terms) if pexels_key else [],
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

    wiki_pool    = []
    wiki_infobox = None
    wiki_title   = script.get("wiki_title", "")
    if wiki_title:
        print(f"Fetching Wikipedia images for '{wiki_title}'...")
        wiki_infobox = fetch_wikipedia_pageimage(wiki_title)
        wiki_pool    = fetch_wikipedia_images(wiki_title)
        print(f"  infobox: {'found' if wiki_infobox else 'none'}  "
              f"article pool: {len(wiki_pool)} licensed images\n")

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
        animal = script.get("animal", "")
        print(f"  Line {lid:2d}: {keyword}")
        c = fetch_candidates(keyword, pexels_key, pixabay_key, flickr_key=flickr_key,
                             wiki_url=wiki_url, animal=animal, line_text=line.get("text", ""))
        if not c["wiki_keyword"] and fallback_keyword and fallback_keyword != keyword:
            print(f"    [fallback] no wiki match — trying: {fallback_keyword}")
            c["wiki_keyword"] = wikipedia_keyword_search(fallback_keyword)
        total = sum(len(v) for v in c.values())
        print(f"    wiki:{len(c['wikipedia'])}  wiki_kw:{len(c['wiki_keyword'])}  flickr:{len(c['flickr'])}  commons:{len(c['commons'])}  pexels:{len(c['pexels'])}  pixabay:{len(c['pixabay'])}  inat:{len(c['inaturalist'])}  gbif:{len(c['gbif'])}  pexels_vid:{len(c['pexels_video'])}  total:{total}")
        all_candidates[str(lid)] = {
            "text":    line["text"],
            "keyword": keyword,
            "sources": {**c, "wiki_infobox": [wiki_infobox] if wiki_infobox else []},
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
            url = (sources["wikipedia"] or sources["wiki_keyword"] or sources["flickr"] or sources["commons"] or sources["pexels"] or sources["pixabay"] or sources["inaturalist"] or sources["gbif"] or [None])[0]
            if not url:
                print(f"  Line {lid_str}: no result from any source")
                failed += 1
                continue
            try:
                download(url, dest)
                for src in ("wikipedia", "wiki_keyword", "flickr", "commons", "pexels", "pixabay", "inaturalist", "gbif"):
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
