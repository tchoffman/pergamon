"""Turn a region someone typed ("us-georgia", "us-northcarolina",
"germany-berlin") into the files OsmAnd publishes for it, using OsmAnd's own
index: the street map plus optional layers (contour lines, hillshade, ...).

    python -m rag.osm_regions us-georgia                       # street map file name
    python -m rag.osm_regions us-georgia streets,contours-feet  # url<TAB>file<TAB>layer per line
"""
from __future__ import annotations

import re
import sys
import urllib.request

INDEX_URL = "https://download.osmand.net/get_indexes"
DOWNLOAD_URL = "https://download.osmand.net/download"
CONTINENTS = ("northamerica", "southamerica", "centralamerica", "europe", "asia", "africa",
              "australia-oceania", "oceania", "russia", "antarctica")
_SUFFIX = re.compile(r"_2\.obf\.zip$")

# layer → (OsmAnd index type, file name from the region's base name, download flag)
LAYERS = {
    "streets":         ("map",       "{b}_2.obf.zip",       "standard"),
    "contours-feet":   ("srtm_map",  "{b}_2.srtmf.obf.zip", "srtmcountry"),
    "contours-meters": ("srtm_map",  "{b}_2.srtm.obf.zip",  "srtmcountry"),
    "hillshade":       ("hillshade", "Hillshade_{b}.sqlitedb", "hillshade"),
    "slope":           ("slope",     "Slope_{b}.sqlitedb",  "slope"),
    "wikipedia":       ("wikimap",   "{b}_2.wiki.obf.zip",  "wiki"),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def index_entries(index_xml: str) -> dict[str, str]:
    """{file name: type} for everything OsmAnd offers."""
    return {name: typ for typ, name in
            re.findall(r'<[a-z]+ type="([a-z_]+)"[^>]*\bname="([^"]+)"', index_xml)}


def map_files(index_xml: str) -> list[str]:
    return [n for n, t in index_entries(index_xml).items() if t == "map"]


def resolve(region: str, files: list[str]) -> tuple[str | None, list[str]]:
    """(street map file name, []) when exactly one map fits, else (None, candidates)."""
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


def layer_files(map_file: str, layers: list[str], entries: dict[str, str]
                ) -> tuple[list[tuple[str, str, str]], list[str]]:
    """([(url, file, layer)], [layers OsmAnd doesn't have for this region])."""
    base = _SUFFIX.sub("", map_file)
    found, missing = [], []
    for layer in layers:
        if layer not in LAYERS:
            missing.append(f"{layer} (unknown layer; choose from {', '.join(LAYERS)})")
            continue
        typ, pattern, flag = LAYERS[layer]
        name = pattern.format(b=base)
        if entries.get(name) == typ:
            found.append((f"{DOWNLOAD_URL}?{flag}=yes&file={name}", name, layer))
        else:
            missing.append(layer)
    return found, missing


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python -m rag.osm_regions <region> [layer,layer...]", file=sys.stderr)
        return 2
    with urllib.request.urlopen(INDEX_URL, timeout=60) as r:
        entries = index_entries(r.read().decode("utf-8", errors="ignore"))
    name, others = resolve(argv[0], [n for n, t in entries.items() if t == "map"])
    if not name:
        if others:
            print(f"'{argv[0]}' could mean: {', '.join(others)}", file=sys.stderr)
        else:
            print(f"No OsmAnd map called '{argv[0]}'. Names look like us-georgia or germany-berlin.",
                  file=sys.stderr)
        return 1
    if len(argv) == 1:
        print(name)
        return 0
    found, missing = layer_files(name, [x.strip() for x in argv[1].split(",") if x.strip()], entries)
    for layer in missing:
        print(f"OsmAnd has no {layer} for {argv[0]}", file=sys.stderr)
    for url, file, layer in found:
        print(f"{url}\t{file}\t{layer}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
