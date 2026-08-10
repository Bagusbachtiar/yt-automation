#!/usr/bin/env python3
"""
Coverage checker: query iNaturalist, GBIF, and Wikimedia Commons for an animal
and return observation/image counts. Used to pre-screen candidate animals before
committing to full pipeline production.

No API key required. Each call makes 3 lightweight HTTP requests (1 result each).
Safe to run concurrently in a thread pool.
"""

import json
import ssl
import urllib.request
import urllib.parse
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_UA = "yt-automation/1.0 (bagusbachtiar50@gmail.com)"

GREEN_INAT   = 100   # >= this: strong wildlife photo coverage
YELLOW_INAT  = 20    # >= this: some coverage, workable
GREEN_VIDEO  = 6     # >= this: good filtered portrait clip count (matches S4 pipeline)
YELLOW_VIDEO = 3     # >= this: borderline workable


def _load_pexels_key() -> str:
    env = Path(__file__).parent / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("PEXELS_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def _get(url: str, extra_headers: dict | None = None) -> dict:
    h = {"User-Agent": _UA}
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
        return json.loads(r.read())


def _pexels_video_count(query: str, api_key: str, require: list[str] | None = None) -> int:
    """Filtered portrait-clip count matching the S4 pipeline's require_terms logic."""
    if require is None:
        words = query.lower().split()
        require = words if len(words) == 1 else [words[-1]]
    params = urllib.parse.urlencode({"query": query, "per_page": 80, "orientation": "portrait"})
    data = _get(f"https://api.pexels.com/videos/search?{params}",
                extra_headers={"Authorization": api_key})
    count = 0
    for v in data.get("videos", []):
        thumb = v.get("image", "").split("/")[-1].split("?")[0].lower()
        text  = (v.get("url", "").lower() + " " + thumb).replace("-", "")
        if all(t in text for t in require):
            count += 1
    return count


def _inat_count(animal: str) -> int:
    """CC-licensed observation photos on iNaturalist."""
    params = urllib.parse.urlencode({
        "q":        animal,
        "photos":   "true",
        "per_page": 1,
        "license":  "cc0,cc-by,cc-by-sa,cc-by-nc,cc-by-nc-sa",
    })
    return _get(f"https://api.inaturalist.org/v1/observations?{params}").get("total_results", 0)


def _gbif_count(animal: str) -> int:
    """Georeferenced occurrence records with images on GBIF."""
    params = urllib.parse.urlencode({"q": animal, "mediaType": "StillImage", "limit": 0})
    return _get(f"https://api.gbif.org/v1/occurrence/search?{params}").get("count", 0)


def _commons_count(animal: str) -> int:
    """Image files on Wikimedia Commons matching the animal name."""
    params = urllib.parse.urlencode({
        "action":      "query",
        "list":        "search",
        "srsearch":    animal,
        "srnamespace": 6,
        "srlimit":     1,
        "srprop":      "",
        "format":      "json",
    })
    data = _get(f"https://commons.wikimedia.org/w/api.php?{params}")
    return data.get("query", {}).get("searchinfo", {}).get("totalhits", 0)


_SPACE_WORDS = {
    "saturn","jupiter","mars","moon","sun","solar","galaxy","nebula","black hole",
    "asteroid","comet","planet","orbit","nasa","space","star","cosmos","universe",
    "exoplanet","quasar","supernova","neutron star","dark matter","dark energy",
    "big bang","milky way","telescope","rings","aurora","meteor","pulsar",
}

GREEN_NASA  = 20   # >= this: good NASA image coverage


_STRIP_Q = None  # compiled lazily

def _strip_question(topic: str) -> str:
    """Strip question prefix + filler verbs. Two-pass handles 'how do', 'why is', 'what causes'."""
    import re as _re
    t = topic.lower().strip().rstrip("?.")
    _q = r'(?:why|how|what|when|where|is|does|can|do|did|will|are|was|were|has|have|could|would|should)'
    t = _re.sub(rf'^\s*{_q}\s+{_q}?\s*', '', t).strip()  # strip 1-2 leading question words
    t = _re.sub(r'\b(?:has|have|are|is|was|were|exist|gets?|makes?|does|do|the|a|an)\b', '', t)
    return ' '.join(t.split())


_SCI_STOP = frozenset({
    "the","a","an","of","in","at","to","for","and","or","is","was","by",
    "with","their","almost","very","some","each","other","so","too","yet",
    "also","just","even","still","only","about","over","under","into","onto",
})


def _nasa_count(topic: str) -> int:
    """Total hits on NASA Images API (no key required)."""
    params = urllib.parse.urlencode({"q": _strip_question(topic), "media_type": "image"})
    data = _get(f"https://images-api.nasa.gov/search?{params}")
    return data.get("collection", {}).get("metadata", {}).get("total_hits", 0)


def _pexels_photo_count(topic: str, api_key: str) -> int:
    params = urllib.parse.urlencode({"query": topic, "per_page": 1})
    return _get(
        f"https://api.pexels.com/v1/search?{params}",
        extra_headers={"Authorization": api_key},
    ).get("total_results", 0)


def check_coverage(topic: str, channel: str = "faunaworks") -> dict:
    """
    Query sources appropriate for the channel and return coverage counts + tiers.
    faunaworks: iNat + GBIF + Commons + Pexels video.
    science:    NASA (if space topic) + Commons + Pexels photo + Pexels video.
    """
    pexels_key = _load_pexels_key()

    if channel == "science":
        result = {"animal": topic, "inat": 0, "gbif": 0, "commons": 0, "video": 0, "nasa": 0}
        is_space = any(w in topic.lower() for w in _SPACE_WORDS)
        # Strip question phrasing so "How do butterflies taste with their feet?"
        # becomes "butterflies taste with their feet" for image searches
        stripped = _strip_question(topic)
        if is_space:
            try:
                result["nasa"] = _nasa_count(topic)  # _nasa_count strips internally
            except Exception:
                pass
        try:
            result["commons"] = _commons_count(stripped)
        except Exception:
            pass
        if pexels_key:
            try:
                # Use first meaningful noun of stripped query as require_term
                _req_words = [w for w in stripped.split() if w not in _SCI_STOP and len(w) > 2]
                _sci_req   = [_req_words[0]] if _req_words else []
                result["video"] = _pexels_video_count(stripped, pexels_key, require=_sci_req)
            except Exception:
                pass
        # Photo tier: space → best of NASA or Commons; general science → Commons
        photo_score  = max(result["nasa"], result["commons"]) if is_space else result["commons"]
        green_photo  = GREEN_NASA if is_space else 50
        yellow_photo = 5 if is_space else 10
        result["tier"] = "green" if photo_score >= green_photo else "yellow" if photo_score >= yellow_photo else "red"
    else:
        result = {"animal": topic, "inat": 0, "gbif": 0, "commons": 0, "video": 0}
        try:
            result["inat"] = _inat_count(topic)
        except Exception:
            pass
        try:
            result["gbif"] = _gbif_count(topic)
        except Exception:
            pass
        try:
            result["commons"] = _commons_count(topic)
        except Exception:
            pass
        if pexels_key:
            try:
                result["video"] = _pexels_video_count(topic, pexels_key)
            except Exception:
                pass
        inat = result["inat"]
        result["tier"] = "green" if inat >= GREEN_INAT else "yellow" if inat >= YELLOW_INAT else "red"

    video = result["video"]
    result["video_tier"] = "green" if video >= GREEN_VIDEO else "yellow" if video >= YELLOW_VIDEO else "red"
    return result


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    channel = "faunaworks"
    if "--channel" in args:
        idx = args.index("--channel")
        channel = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    topic = " ".join(args) or "mantis shrimp"
    print(f"Checking: {topic}  (channel: {channel})")
    r = check_coverage(topic, channel=channel)
    print(f"  iNat: {r['inat']:,}  GBIF: {r['gbif']:,}  Commons: {r['commons']:,}  "
          f"NASA: {r.get('nasa', 0):,}  Pexels video: {r['video']:,}  "
          f"tier: {r['tier']}  video_tier: {r['video_tier']}")
