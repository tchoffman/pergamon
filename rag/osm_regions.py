"""Turn a region someone typed ("us-georgia", "us-northcarolina",
"germany-berlin") into the file name OsmAnd publishes
("Us_georgia_northamerica_2.obf.zip"), using OsmAnd's own index.

    python -m rag.osm_regions us-georgia    # prints the file name, or explains
"""
from __future__ import annotations

import re
import sys
import urllib.request

INDEX_URL = "https://download.osmand.net/get_indexes"
CONTINENTS = ("northamerica", "southamerica", "centralamerica", "europe", "asia", "africa",
              "australia-oceania", "oceania", "russia", "antarctica")
_SUFFIX = re.compile(r"_2\.obf\.zip$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def map_files(index_xml: str) -> list[str]:
    return re.findall(r'<region type="map"[^>]*\bname="([^"]+\.obf\.zip)"', index_xml)


def resolve(region: str, files: list[str]) -> tuple[str | None, list[str]]:
    """(file name, []) when exactly one map fits, else (None, candidates)."""
    want = _norm(_SUFFIX.sub("", region.strip()))
    if not want:
        return None, []
    exact, prefixed = [], []
    for f in files:
        base = _norm(_SUFFIX.sub("", f))
        if base == want:
            exact.append(f)
        elif base.startswith(want) and base[len(want):] in {_norm(c) for c in CONTINENTS}:
            prefixed.append(f)
    hits = exact or prefixed
    if len(hits) == 1:
        return hits[0], []
    if hits:
        return None, hits
    near = [f for f in files if want[:6] and _norm(f).startswith(want[:6])]
    return None, near[:8]


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python -m rag.osm_regions <region>", file=sys.stderr)
        return 2
    with urllib.request.urlopen(INDEX_URL, timeout=60) as r:
        files = map_files(r.read().decode("utf-8", errors="ignore"))
    name, others = resolve(argv[0], files)
    if name:
        print(name)
        return 0
    if others:
        print(f"'{argv[0]}' could mean: {', '.join(others)}", file=sys.stderr)
    else:
        print(f"No OsmAnd map called '{argv[0]}'. Names look like us-georgia or germany-berlin.",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
