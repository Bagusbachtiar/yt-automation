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

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_UA = "yt-automation/1.0 (bagusbachtiar50@gmail.com)"

GREEN_INAT  = 100   # >= this: strong wildlife photo coverage
YELLOW_INAT = 20    # >= this: some coverage, workable


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as r:
        return json.loads(r.read())


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
    Query iNaturalist, GBIF, and Commons for photo coverage of an animal.
    Returns {"animal", "inat", "gbif", "commons", "tier"}.
    Tier is based on iNaturalist count (most reliable wildlife indicator):
      green  >= 100 observations
      yellow >= 20  observations
      red    <  20  observations
    """
    result = {"animal": animal, "inat": 0, "gbif": 0, "commons": 0}
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

    inat = result["inat"]
    result["tier"] = "green" if inat >= GREEN_INAT else "yellow" if inat >= YELLOW_INAT else "red"
    return result


if __name__ == "__main__":
    import sys
    animal = " ".join(sys.argv[1:]) or "mantis shrimp"
    print(f"Checking: {animal}")
    r = check_coverage(animal)
    print(f"  iNat: {r['inat']:,}  GBIF: {r['gbif']:,}  Commons: {r['commons']:,}  tier: {r['tier']}")
