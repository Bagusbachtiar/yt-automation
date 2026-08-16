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

from wikipedia_fetch import (fetch_wikipedia_images, fetch_wikipedia_images_with_captions,
                             fetch_wikipedia_pageimage, is_acceptable_license, search_wikipedia)

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

# Generic first-word modifiers that do NOT distinguish an animal from others in the same family.
# When the first word of a 2+-word animal name is in this set, require only the last word in
# Pexels/Pixabay searches (e.g. "Red Fox" → require "fox", not "red fox").
# When NOT in this set, require both words (e.g. "Harpy Eagle" → require ["harpy","eagle"]).
_GENERIC_FIRST = frozenset({
    "white","red","blue","black","brown","gray","grey","green","yellow","orange","golden","silver",
    "great","greater","giant","large","big","small","little","lesser","common","dwarf","long","short",
    "american","african","asian","european","australian",
    "eastern","western","northern","southern","north","south","east","west",
    "spotted","striped","horned","tailed","ringed",
})

# Food/commercial context suppression — applied to Pexels alt text and Pixabay tags.
# Catches food-fish problem: tuna/salmon results dominated by sushi plates and market counters.
# Only applied when the image keyword and script line text don't signal a fishing/industry topic
# (so lines about overfishing can still surface fishing boat imagery).
_FOOD_SIGNALS = frozenset({
    # Food preparation / serving
    "sushi", "sashimi", "fillet", "filet",
    "seafood", "fishmonger",
    "grilled", "smoked", "fried", "baked", "cooked", "cooking",
    "plate", "dish", "meal", "recipe", "food", "steak",
    "canned", "grocery", "supermarket", "restaurant",
    "cutting board",
    # Raw / dead food context
    "raw fish", "raw salmon", "raw tuna", "fresh catch",
    # Commercial / market
    "fish market", "market stall", "seafood market",
    # Industrial fishing — excluded unless line is about overfishing/conservation
    "fishing boat", "fishing net", "fishing vessel", "fish haul",
})
def _url_no_food(url: str, excl: frozenset) -> bool:
    """True if URL filename doesn't contain any food signal. Fast filter for Commons/wiki URLs."""
    if not excl:
        return True
    name = url.split("/")[-1].split("?")[0].lower().replace("_", " ").replace("-", " ")
    return not any(e in name for e in excl)


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
            if (not incl or all(t in r["_text"].replace("-", "") for t in incl))
            and (not excl or not any(e in r["_text"].replace("-", " ") for e in excl))
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


def nasa_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict]:
    """NASA Images API — no key required. Returns portrait-friendly images."""
    params = urllib.parse.urlencode({"q": query, "media_type": "image", "page_size": min(limit * 4, 30)})
    try:
        req = urllib.request.Request(
            f"https://images-api.nasa.gov/search?{params}",
            headers={"User-Agent": "yt-automation/1.0"},
        )
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        results = []
        for item in data.get("collection", {}).get("items", []):
            links = item.get("links", [])
            thumb = next((l["href"] for l in links if "thumb" in l.get("href", "")), None)
            if not thumb:
                continue
            full = thumb.replace("~thumb.jpg", "~large.jpg") if "~thumb.jpg" in thumb else thumb
            results.append({"url": full, "thumb": thumb})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"    [NASA] error: {e}")
        return []


def openverse_search(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[str]:
    """CC/public-domain images from Openverse (aggregates Flickr, Wikimedia, Smithsonian, NASA…).
    # ponytail: 100 req/day unauthenticated; set OPENVERSE_API_KEY in .env for 10k/day
    """
    params = urllib.parse.urlencode({
        "q":            query,
        "page_size":    min(limit * 3, 20),
        "license_type": "commercial",
    })
    headers = {"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"}
    ov_key = os.environ.get("OPENVERSE_API_KEY", "").strip()
    if ov_key:
        headers["Authorization"] = f"Bearer {ov_key}"
    try:
        req = urllib.request.Request(f"https://api.openverse.org/v1/images/?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        return [item["url"] for item in data.get("results", []) if item.get("url")][:limit]
    except Exception as e:
        print(f"    [Openverse] error: {e}")
        return []


def commons_search_with_captions(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict]:
    """Like commons_search but returns [{"url": ..., "caption": ...}] for mythology channel."""
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
            "iiextmetadatafilter": "LicenseShortName|ObjectName|ImageDescription",
            "format": "json",
        })
        req = urllib.request.Request(
            f"{COMMONS_API}?{params}",
            headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        pages = data.get("query", {}).get("pages", {})
        results = []
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            license_str = (info.get("extmetadata", {})
                               .get("LicenseShortName", {})
                               .get("value", ""))
            img_url = info.get("thumburl") or info.get("url", "")
            if not img_url or not any(img_url.lower().split("?")[0].endswith(ext) for ext in COMMONS_IMAGE_EXTS):
                continue
            if not is_acceptable_license(license_str):
                continue
            meta = info.get("extmetadata", {})
            obj_name = (meta.get("ObjectName") or {}).get("value", "")
            raw_desc = (meta.get("ImageDescription") or {}).get("value", "")
            desc = re.sub(r"<[^>]+>", "", raw_desc).strip()[:80]
            page_title = page.get("title", "").replace("File:", "").rsplit(".", 1)[0].replace("_", " ")
            caption = obj_name or desc or page_title
            results.append({"url": img_url, "caption": caption})
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        print(f"    [Commons+captions] error: {e}")
    return []


def openverse_search_with_captions(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict]:
    """Like openverse_search but returns [{"url": ..., "caption": ...}] for mythology channel."""
    params = urllib.parse.urlencode({
        "q":            query,
        "page_size":    min(limit * 3, 20),
        "license_type": "commercial",
    })
    headers = {"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"}
    ov_key = os.environ.get("OPENVERSE_API_KEY", "").strip()
    if ov_key:
        headers["Authorization"] = f"Bearer {ov_key}"
    try:
        req = urllib.request.Request(f"https://api.openverse.org/v1/images/?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
            data = json.loads(r.read())
        results = []
        for item in data.get("results", []):
            url = item.get("url", "")
            if not url:
                continue
            title   = item.get("title", "")
            creator = item.get("creator", "")
            caption = f"{title} — {creator}" if title and creator else title or creator
            results.append({"url": url, "caption": caption})
        return results[:limit]
    except Exception as e:
        print(f"    [Openverse+captions] error: {e}")
        return []


def wikipedia_keyword_search_with_captions(query: str, limit: int = CANDIDATES_PER_SOURCE) -> list[dict]:
    """Like wikipedia_keyword_search but returns dicts with captions for mythology channel."""
    title = search_wikipedia(query)
    if not title or not _relevant_overlap(query, title):
        return []
    return fetch_wikipedia_images_with_captions(title, limit=limit)


def fetch_candidates(query: str, pexels_key: str, pixabay_key: str,
                     flickr_key: str = "", wiki_url=None,
                     wiki_caption: str = "", animal: str = "", line_text: str = "",
                     channel: str = "faunaworks") -> dict:
    # Mythology: Wikipedia article images + Commons keyword search + Openverse only.
    # No Pexels/Pixabay/iNat/GBIF/NASA — stock photo sites don't have classical art.
    if channel == "mythology":
        wiki_entry = ({"url": wiki_url, "caption": wiki_caption} if wiki_caption and wiki_url
                      else ([wiki_url] if wiki_url else []))
        wiki_cands = [wiki_entry] if wiki_entry else []
        return {
            "wikipedia":    wiki_cands,
            "wiki_keyword": wikipedia_keyword_search_with_captions(query),
            "commons":      commons_search_with_captions(query),
            "openverse":    openverse_search_with_captions(query),
            "flickr": [], "pexels": [], "pixabay": [], "inaturalist": [],
            "gbif": [], "pexels_video": [], "nasa": [],
        }

    # Science topics are question-phrased ("How auroras happen") — term filtering
    # would key on "happen", killing all results. Skip for science; query handles relevance.
    is_science = channel == "science"
    words = re.split(r'[\s\-]+', animal.lower()) if animal else []
    if is_science:
        # Build require_terms from the image_keyword itself so multi-word descriptive
        # phrases don't pull clips that match only one generic word ("surface", "tracks").
        qwords = query.lower().split()
        if len(qwords) >= 3:
            require_terms = qwords[-2:]  # ponytail: both last 2 words must appear; upgrade to phrase-match if still collisions
        elif len(qwords) >= 1:
            require_terms = [qwords[-1]]
        else:
            require_terms = []
    elif len(words) == 1:
        require_terms = words
    elif words[0] in _GENERIC_FIRST:
        require_terms = [words[-1]]
    else:
        require_terms = [words[0], words[-1]]

    # Suppress food/commercial imagery (sushi, fillet, plate…) unless the keyword or
    # line text signals that fishing/industry context is intentional (overfishing lines,
    # conservation lines — those should be able to surface boat/net imagery).
    fishing_context = bool(set(query.lower().split()) & _FISHING_QUERY_SIGNALS) or \
                      any(t in line_text.lower() for t in _FISHING_LINE_SIGNALS)
    exclude_terms = frozenset() if fishing_context else _FOOD_SIGNALS

    _nf = lambda u: _url_no_food(u, exclude_terms)
    return {
        "wikipedia":    [wiki_url] if wiki_url and _nf(wiki_url) else [],
        "wiki_keyword": [u for u in wikipedia_keyword_search(query) if _nf(u)],
        "flickr":       [u for u in flickr_commons_search(query, flickr_key) if _nf(u)] if flickr_key else [],
        "commons":      [u for u in commons_search(query) if _nf(u)],
        "pexels":       _pexels_with_fallback(query, pexels_key, pexels_search, require_terms, exclude_terms)       if pexels_key else [],
        "pixabay":      pixabay_search(query, pixabay_key, require_terms=require_terms, exclude_terms=exclude_terms) if pixabay_key else [],
        "inaturalist":  [] if is_science else inaturalist_search(animal or query),
        "gbif":         [] if is_science else gbif_search(animal or query),
        "pexels_video": _pexels_with_fallback(query, pexels_key, pexels_video_search, require_terms, exclude_terms) if pexels_key else [],
        "nasa":         nasa_search(query) if is_science else [],
        "openverse":    [u for u in openverse_search(query) if _nf(u)],
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
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument("--channel", default="faunaworks")
    _args, _ = _parser.parse_known_args()
    channel = _args.channel

    load_env()
    pexels_key  = os.environ.get("PEXELS_API_KEY",  "").strip()
    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    flickr_key  = os.environ.get("FLICKR_API_KEY",  "").strip()
    tg_token    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    is_mythology = channel == "mythology"
    if not is_mythology and not pexels_key and not pixabay_key and not flickr_key:
        sys.exit("[ERROR] No API keys. Set PEXELS_API_KEY, PIXABAY_API_KEY, or FLICKR_API_KEY in .env")
    if not SCRIPT_JSON.exists():
        sys.exit(f"[ERROR] {SCRIPT_JSON} not found.")

    script = json.loads(SCRIPT_JSON.read_text(encoding="utf-8"))
    lines  = script["lines"]
    print(f"\nCollecting candidates for {len(lines)} lines...\n")

    wiki_pool    = []      # list of URL strings (or dicts for mythology)
    wiki_captions = {}    # url → caption str, populated for mythology only
    wiki_infobox = None
    wiki_title   = script.get("wiki_title", "")
    if wiki_title:
        print(f"Fetching Wikipedia images for '{wiki_title}'...")
        if is_mythology:
            _entries = fetch_wikipedia_images_with_captions(wiki_title)
            wiki_pool    = [e["url"] for e in _entries]
            wiki_captions = {e["url"]: e.get("caption", "") for e in _entries}
        else:
            wiki_infobox = fetch_wikipedia_pageimage(wiki_title)
            wiki_pool    = fetch_wikipedia_images(wiki_title)
        print(f"  infobox: {'found' if wiki_infobox else 'none'}  "
              f"article pool: {len(wiki_pool)} licensed images\n")

    IMAGES_DIR.mkdir(exist_ok=True)
    all_candidates = {}
    used_wiki = set()

    # Fetch a global video pool once — per-line keywords are too narrow for video.
    # Mythology has no video source; skip.
    global_vids = []
    animal_global = script.get("animal") or script.get("topic", "")
    is_science_global = channel == "science"
    if pexels_key and animal_global and not is_mythology:
        if is_science_global:
            from coverage_check import _strip_question, _SCI_STOP
            _vid_q = _strip_question(animal_global)
            _words = [w for w in _vid_q.split() if w not in _SCI_STOP and len(w) > 2]
            _req   = [_words[0]] if _words else []
        else:
            _vid_q = animal_global
            _awords = re.split(r'[\s\-]+', animal_global.lower())
            _req = [_awords[0], _awords[-1]] if len(_awords) >= 2 and _awords[0] not in _GENERIC_FIRST else [_awords[-1]]
        global_vids = pexels_video_search(_vid_q, pexels_key, limit=20,
                                          require_terms=_req, exclude_terms=_FOOD_SIGNALS) or []
        print(f"Global video pool: {len(global_vids)} clips for '{_vid_q}'\n")

    for idx, line in enumerate(lines):
        lid = line["id"]
        keyword = (
            line.get("image_keyword")
            or (line.get("image_keywords") or [None])[0]
            or line["text"]
        )
        fallback_keyword = line.get("image_keyword_fallback", "")
        wiki_url     = None
        wiki_caption_line = ""
        if wiki_pool:
            wiki_url = _best_wiki_match(keyword, wiki_pool, used_wiki)
            if wiki_url:
                used_wiki.add(wiki_url)
                wiki_caption_line = wiki_captions.get(wiki_url, "")
        # list mode has no "animal" key; use the per-line keyword as the filter anchor
        animal = script.get("animal") or script.get("topic") or keyword
        print(f"  Line {lid:2d}: {keyword}")
        c = fetch_candidates(keyword, pexels_key, pixabay_key, flickr_key=flickr_key,
                             wiki_url=wiki_url, wiki_caption=wiki_caption_line,
                             animal=animal, line_text=line.get("text", ""),
                             channel=channel)
        if global_vids:
            seen_gv = {e["url"] for e in c["pexels_video"] if isinstance(e, dict)}
            c["pexels_video"] += [v for v in global_vids if v["url"] not in seen_gv]
        if not c["wiki_keyword"] and fallback_keyword and fallback_keyword != keyword:
            print(f"    [fallback] no wiki match — trying: {fallback_keyword}")
            c["wiki_keyword"] = wikipedia_keyword_search(fallback_keyword)
        # Pexels fallback: if primary keyword returns thin results, retry with fallback
        if fallback_keyword and fallback_keyword != keyword and pexels_key:
            if len(c["pexels"]) < 2:
                fb = pexels_search(fallback_keyword, pexels_key, limit=CANDIDATES_PER_SOURCE)
                if fb:
                    print(f"    [pexels fallback] '{keyword}' thin ({len(c['pexels'])}) → '{fallback_keyword}': +{len(fb)}")
                    c["pexels"] = c["pexels"] + fb
            if len(c["pexels_video"]) < 2:
                fb_v = pexels_video_search(fallback_keyword, pexels_key, limit=CANDIDATES_PER_SOURCE)
                if fb_v:
                    print(f"    [pexels_video fallback] '{keyword}' thin ({len(c['pexels_video'])}) → '{fallback_keyword}': +{len(fb_v)}")
                    c["pexels_video"] = c["pexels_video"] + fb_v
        total = sum(len(v) for v in c.values())
        print(f"    wiki:{len(c['wikipedia'])}  wiki_kw:{len(c['wiki_keyword'])}  flickr:{len(c['flickr'])}  commons:{len(c['commons'])}  openverse:{len(c['openverse'])}  pexels:{len(c['pexels'])}  pixabay:{len(c['pixabay'])}  inat:{len(c['inaturalist'])}  gbif:{len(c['gbif'])}  pexels_vid:{len(c['pexels_video'])}  nasa:{len(c['nasa'])}  total:{total}")
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
            url = (sources["wikipedia"] or sources["wiki_keyword"] or sources["flickr"] or sources["commons"] or sources["openverse"] or sources["pexels"] or sources["pixabay"] or sources["inaturalist"] or sources["gbif"] or [None])[0]
            if not url:
                print(f"  Line {lid_str}: no result from any source")
                failed += 1
                continue
            try:
                download(url, dest)
                for src in ("wikipedia", "wiki_keyword", "flickr", "commons", "openverse", "pexels", "pixabay", "inaturalist", "gbif"):
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
