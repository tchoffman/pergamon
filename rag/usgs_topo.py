"""Plan USGS topographic map downloads for an area (United States only).

Two editions, both free, public-domain GeoPDFs from The National Map:
  current     the newest US Topo map for each 7.5-minute quad: contour lines,
              streams, ponds, lakes, wetlands, woodland, trails, roads
  historical  the oldest edition at the most detailed scale for each quad
              (often 1900s-1950s): springs, old homesteads, orchards, mills,
              mines and cemeteries from before the land was developed

A GeoPDF opens in any PDF viewer; GPS-aware apps (Avenza Maps, QGIS, ...)
also show where you are on it.

    python -m rag.usgs_topo "Asheville, NC" 10 current,historical   # url<TAB>file<TAB>label lines
"""
from __future__ import annotations

import json
import math
import re
import sys
import urllib.parse
import urllib.request

API = "https://tnmaccess.nationalmap.gov/api/v1/products"
GEOCODER = "https://nominatim.openstreetmap.org/search"
DATASETS = {"current": "US Topo", "historical": "Historical Topographic Maps"}
_SCALE = re.compile(r"1:(\d+)-scale")


def _get_json(url: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={"User-Agent": "Pergamon-installer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def locate(area: str) -> tuple[float, float]:
    """'35.6,-82.5' or a place name ('Asheville, NC') → (lat, lon)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", area)
    if m:
        return float(m.group(1)), float(m.group(2))
    q = urllib.parse.urlencode({"q": area, "format": "json", "limit": 1, "countrycodes": "us"})
    hits = _get_json(f"{GEOCODER}?{q}", timeout=30)
    if not hits:
        raise LookupError(f"couldn't find a US place called '{area}'")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


def bbox(lat: float, lon: float, radius_mi: float) -> tuple[float, float, float, float]:
    dlat = radius_mi / 69.0
    dlon = radius_mi / (69.0 * max(math.cos(math.radians(lat)), 0.01))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def products(dataset: str, box: tuple[float, ...], fetch=_get_json) -> list[dict]:
    items, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"datasets": dataset, "bbox": ",".join(f"{v:.5f}" for v in box),
                                    "max": 500, "offset": offset})
        page = fetch(f"{API}?{q}")
        batch = page.get("items") or []
        items += batch
        offset += len(batch)
        if not batch or offset >= int(page.get("total") or 0):
            return items


def _quad(item: dict) -> str:
    """'USGS 1:24000-scale Quadrangle for Asheville, NC 1936' → 'Asheville, NC'."""
    t = item["title"]
    t = re.sub(r"^.*?(?:map|Quadrangle) for ", "", t)
    return re.sub(r"\s+\d{4}$", "", t).strip()


def _year(item: dict) -> str:
    return (item.get("publicationDate") or "0000")[:4]


def pick_current(items: list[dict]) -> list[dict]:
    """The newest US Topo per quad."""
    best: dict[str, dict] = {}
    for it in items:
        if "7.5-minute" not in it["title"] or not it["downloadURL"].endswith(".pdf"):
            continue
        q = _quad(it)
        if q not in best or (it.get("publicationDate") or "") > (best[q].get("publicationDate") or ""):
            best[q] = it
    return [best[k] for k in sorted(best)]


def pick_historical(items: list[dict]) -> list[dict]:
    """Per quad: the most detailed scale available, then its oldest edition."""
    pdfs = [it for it in items if it["downloadURL"].endswith(".pdf") and _SCALE.search(it["title"])]
    finest: dict[str, int] = {}
    for it in pdfs:
        s = int(_SCALE.search(it["title"]).group(1))
        finest[_quad(it)] = min(s, finest.get(_quad(it), s))
    best: dict[str, dict] = {}
    for it in pdfs:
        q, s = _quad(it), int(_SCALE.search(it["title"]).group(1))
        if s != finest[q] or s > 62500:  # 1:125,000 and coarser show too little to navigate by
            continue
        if q not in best or _year(it) < _year(best[q]):
            best[q] = it
    return [best[k] for k in sorted(best)]


def plan(area: str, radius_mi: float, editions: list[str], fetch=_get_json,
         geocode=locate) -> list[tuple[str, str, str]]:
    box = bbox(*geocode(area), radius_mi)
    out = []
    for ed in editions:
        items = products(DATASETS[ed], box, fetch)
        chosen = pick_current(items) if ed == "current" else pick_historical(items)
        for it in chosen:
            url = it["downloadURL"]
            name = urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace(" ", "_")
            out.append((url, name, f"{ed} {_quad(it)} {_year(it)}"))
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('Usage: python -m rag.usgs_topo "<place or lat,lon>" <radius miles> [current,historical]',
              file=sys.stderr)
        return 2
    editions = [e.strip() for e in (argv[2] if len(argv) > 2 else "current").split(",") if e.strip()]
    bad = [e for e in editions if e not in DATASETS]
    if bad:
        print(f"Unknown edition(s): {', '.join(bad)} (choose from {', '.join(DATASETS)})", file=sys.stderr)
        return 2
    try:
        rows = plan(argv[0], float(argv[1]), editions)
    except (LookupError, OSError, ValueError) as e:
        print(f"USGS topo: {e}", file=sys.stderr)
        return 1
    if not rows:
        print(f"No USGS topo maps found around '{argv[0]}' (US only).", file=sys.stderr)
        return 1
    for row in rows:
        print("\t".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
