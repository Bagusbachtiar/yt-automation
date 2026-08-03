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
GREEN_VIDEO  = 10    # >= this: good Pexels video coverage
YELLOW_VIDEO = 5     # >= this: workable video coverage


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


def _pexels_video_count(animal: str, api_key: str) -> int:
    params = urllib.parse.urlencode({"query": animal, "per_page": 1})
    return _get(
        f"https://api.pexels.com/videos/search?{params}",
        extra_headers={"Authorization": api_key},
    ).get("total_results", 0)


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


def check_coverage(animal: str) -> dict:
    """
    Query iNaturalist, GBIF, Commons, and Pexels Video for coverage of an animal.
    Returns {"animal", "inat", "gbif", "commons", "video", "tier"}.
    Tier (photo) based on iNaturalist count:
      green  >= 100 observations
      yellow >= 20  observations
      red    <  20  observations
    video field = Pexels total_results (raw count, unfiltered).
    """
    result = {"animal": animal, "inat": 0, "gbif": 0, "commons": 0, "video": 0}
    try:
        result["inat"] = _inat_count(animal)
    except Exception:
        pass
    try:
        result["gbif"] = _gbif_count(animal)
    except Exception:
        pass
    try:
        result["commons"] = _commons_count(animal)
    except Exception:
        pass

    pexels_key = _load_pexels_key()
    if pexels_key:
        try:
            result["video"] = _pexels_video_count(animal, pexels_key)
        except Exception:
            pass

    inat  = result["inat"]
    video = result["video"]
    result["tier"]       = "green" if inat  >= GREEN_INAT   else "yellow" if inat  >= YELLOW_INAT  else "red"
    result["video_tier"] = "green" if video >= GREEN_VIDEO  else "yellow" if video >= YELLOW_VIDEO else "red"
    return result


if __name__ == "__main__":
    import sys
    animal = " ".join(sys.argv[1:]) or "mantis shrimp"
    print(f"Checking: {animal}")
    r = check_coverage(animal)
    print(f"  iNat: {r['inat']:,}  GBIF: {r['gbif']:,}  Commons: {r['commons']:,}  "
          f"Pexels video: {r['video']:,}  tier: {r['tier']}")
