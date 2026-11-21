"""Offline street map of the hub's region.

At install, a region (a place and a radius) is cut out of the free worldwide
Protomaps map with the `pmtiles` tool, which downloads only the tiles inside
it. The hub serves that one file plus the map viewer (MapLibre), its fonts and
icons, so phones on the hub's Wi-Fi get a normal pan-and-zoom map with no
internet.

Regions can be described several ways:

    pergamon maps install "Keller, TX"              # a town or county: its own boundary
    pergamon maps install "Keller, TX" 25           # 25 miles around it
    pergamon maps install "32.93,-97.23" 25         # lat,lon and a radius
    pergamon maps install "-97.6,32.5,-96.5,33.4"   # a box: west,south,east,north
    pergamon maps install "Tarrant County, TX; Dallas County, TX"   # several places together
    pergamon maps install "North Texas"             # a named region (config/maps/regions.yaml)
    pergamon maps install "Atlanta Metro" --dry-run # just report the size
    pergamon maps status | remove <name>

Several regions can be installed; the Maps page lets people pick one.
"""
from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from rag.config import REPO_ROOT

PMTILES_VERSION = "1.31.2"
# Published SHA-256 digests from the GitHub release of protomaps/go-pmtiles.
PMTILES_BUILDS = {
    ("Darwin", "arm64"): ("go-pmtiles-1.31.2_Darwin_arm64.zip",
                          "40528f7f616fcbf91207cd48c8fc023d213f6d86c0cbf1f748732803d1880f3d"),
    ("Darwin", "x86_64"): ("go-pmtiles-1.31.2_Darwin_x86_64.zip",
                           "1f0dc02eee6c58312dd6c509faee1b5c32f0596568af1bf51f1b034e7a88a65b"),
    ("Linux", "aarch64"): ("go-pmtiles_1.31.2_Linux_arm64.tar.gz",
                           "f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1"),
    ("Linux", "x86_64"): ("go-pmtiles_1.31.2_Linux_x86_64.tar.gz",
                          "3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f"),
}
BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
MAX_ZOOM = 15
UA = {"User-Agent": "Pergamon-maps"}

# The map viewer, pinned (each release at least two weeks old when chosen).
CDN = "https://cdn.jsdelivr.net/npm/"
WEB_FILES = {
    "maplibre-gl.js": CDN + "maplibre-gl@5.24.0/dist/maplibre-gl.js",
    "maplibre-gl.css": CDN + "maplibre-gl@5.24.0/dist/maplibre-gl.css",
    "pmtiles.js": CDN + "pmtiles@4.5.0/dist/pmtiles.js",
    "basemaps.js": CDN + "@protomaps/basemaps@5.7.2/dist/basemaps.js",
}
ASSETS = "https://raw.githubusercontent.com/protomaps/basemaps-assets/main/"
FONTS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic"]
GLYPH_RANGES = ["0-255", "256-511", "8192-8447"]   # Latin, Latin Extended, punctuation
SPRITES = [f"{flavor}{scale}.{ext}" for flavor in ("light", "dark")
           for scale in ("", "@2x") for ext in ("json", "png")]


def maps_dir() -> Path:
    from rag.storage import data_dir
    return data_dir() / "maps"


REGIONS_PATH = REPO_ROOT / "config" / "maps" / "regions.yaml"


def web_dir() -> Path:
    return maps_dir() / "web"


def slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:60] or "region"


def tiles_path(region: str) -> Path:
    return maps_dir() / "regions" / f"{slug(region)}.pmtiles"


def info_path(region: str) -> Path:
    return maps_dir() / "regions" / f"{slug(region)}.json"


def regions() -> list[dict]:
    """Installed regions, largest first."""
    out = []
    for f in sorted((maps_dir() / "regions").glob("*.json")):
        try:
            info = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if (f.with_suffix(".pmtiles")).exists():
            out.append({**info, "slug": f.stem})
    return sorted(out, key=lambda r: -r.get("bytes", 0))


def region_info(region: str) -> dict | None:
    return next((r for r in regions() if r["slug"] == slug(region)), None)


def installed() -> bool:
    return bool(regions()) and (web_dir() / "maplibre-gl.js").exists()


def _get(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


# ───────────────────────────── the pmtiles tool ─────────────────────────────

def pmtiles_bin(get=_get) -> Path:
    """build/bin/pmtiles, downloaded and checked against its published digest."""
    dest = REPO_ROOT / "build" / "bin" / "pmtiles"
    if dest.exists():
        return dest
    key = (platform.system(), platform.machine())
    if key not in PMTILES_BUILDS:
        raise RuntimeError(f"no pmtiles build for {key}; install it and put it on PATH")
    if shutil.which("pmtiles"):
        return Path(shutil.which("pmtiles"))
    name, digest = PMTILES_BUILDS[key]
    blob = get(f"https://github.com/protomaps/go-pmtiles/releases/download/v{PMTILES_VERSION}/{name}")
    if hashlib.sha256(blob).hexdigest() != digest:
        raise RuntimeError("the downloaded pmtiles tool doesn't match its published checksum")
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            data = z.read(next(n for n in z.namelist() if n.rstrip("/").endswith("pmtiles")))
    else:
        with tarfile.open(fileobj=io.BytesIO(blob)) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("pmtiles") and m.isfile())
            data = t.extractfile(member).read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def latest_build(get=_get) -> str:
    builds = json.loads(get(BUILDS_URL, timeout=30))
    return BUILD_BASE + builds[-1]["key"]


# ───────────────────────────── install ─────────────────────────────

class AreaError(ValueError):
    pass


def named_regions() -> dict:
    import yaml
    try:
        return {k.lower(): v for k, v in (yaml.safe_load(REGIONS_PATH.read_text()) or {})["regions"].items()}
    except (OSError, KeyError, TypeError):
        return {}


def lookup(place: str) -> tuple[float, float, list[float] | None]:
    """(lat, lon, [south, north, west, east] boundary or None) for a place name."""
    import time
    import urllib.parse
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    time.sleep(1)  # the free geocoder asks for at most one request a second
    hits = json.loads(_get(f"https://nominatim.openstreetmap.org/search?{q}", timeout=30))
    if not hits:
        raise AreaError(f"couldn't find '{place}'")
    h = hits[0]
    bb = [float(x) for x in h["boundingbox"]] if h.get("class") == "boundary" or h.get("type") in (
        "administrative", "county", "city", "town", "village", "state") else None
    return float(h["lat"]), float(h["lon"]), bb


def region_bbox(area: str, radius_mi: float | None = None, geocode=None) -> tuple[float, float, float, float]:
    """(west, south, east, north) for any of the ways a region can be described."""
    import re
    from rag import usgs_topo
    look = geocode or lookup
    area = area.strip()
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", area)] if re.fullmatch(
        r"[\s\d.,-]+", area) else []
    if len(nums) == 4:                      # west,south,east,north
        w, s_, e, n = nums
        if not (w < e and s_ < n):
            raise AreaError("a box is west,south,east,north")
        return w, s_, e, n
    if len(nums) == 2:                      # lat,lon
        return usgs_topo.bbox(nums[0], nums[1], radius_mi or 25)
    named = named_regions().get(area.lower())
    places = named["places"] if named else [p.strip() for p in area.split(";") if p.strip()]
    if not places:
        raise AreaError("describe the area: a place, lat,lon, a box, or a named region")
    if len(places) == 1 and radius_mi:
        lat, lon, _ = look(places[0])
        return usgs_topo.bbox(lat, lon, radius_mi)
    boxes = []
    for place in places:
        lat, lon, bb = look(place)
        if bb:
            south, north, west, east = bb
            boxes.append((west, south, east, north))
        else:                               # a point (a street, a landmark): 10 miles around it
            boxes.append(usgs_topo.bbox(lat, lon, 10))
    w, s_, e, n = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))
    pad_x, pad_y = (e - w) * 0.03, (n - s_) * 0.03   # a little margin around the edges
    return w - pad_x, s_ - pad_y, e + pad_x, n + pad_y


def extract(area: str, radius_mi: float | None = None, dry_run: bool = False, max_zoom: int = MAX_ZOOM,
            run=subprocess.run, geocode=None, name: str | None = None) -> dict:
    """Cut the region out of the latest worldwide build into regions/<name>.pmtiles."""
    box = region_bbox(area, radius_mi, geocode)
    src = latest_build()
    name = name or (f"{area} ({radius_mi:g} mi)" if radius_mi else area)
    out = tiles_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(pmtiles_bin()), "extract", src, str(out), "--bbox=" + ",".join(f"{v:.5f}" for v in box),
           f"--maxzoom={max_zoom}"]
    if dry_run:
        cmd.append("--dry-run")
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "pmtiles extract failed")
    info = {"name": name, "area": area, "radius_mi": radius_mi, "bbox": list(box), "max_zoom": max_zoom,
            "source": src, "output": (r.stdout + r.stderr).strip()[-400:]}
    if not dry_run:
        info["bytes"] = out.stat().st_size
        info_path(name).write_text(json.dumps(info, indent=1))
    return info


def remove(name: str) -> bool:
    found = False
    for f in (tiles_path(name), info_path(name)):
        if f.exists():
            f.unlink()
            found = True
    return found


def fetch_web(get=_get) -> int:
    """The viewer, fonts and icons, so the map needs nothing from the internet."""
    root = web_dir()
    n = 0
    for name, url in WEB_FILES.items():
        dest = root / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(get(url))
            n += 1
    for font in FONTS:
        for rng in GLYPH_RANGES:
            dest = root / "fonts" / font / f"{rng}.pbf"
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(get(ASSETS + "fonts/" + urllib.request.quote(font) + f"/{rng}.pbf"))
                n += 1
    for sprite in SPRITES:
        dest = root / "sprites" / sprite
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(get(ASSETS + "sprites/v4/" + sprite))
            n += 1
    return n


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "install" and len(argv) >= 2:
            dry = "--dry-run" in argv
            rest = [a for a in argv[2:] if not a.startswith("--")]
            zoom = next((int(a.split("=")[1]) for a in argv if a.startswith("--maxzoom=")), MAX_ZOOM)
            radius = float(rest[0]) if rest else None
            if not dry:
                print(f"Viewer, fonts and icons: {fetch_web()} file(s) downloaded")
            box = region_bbox(argv[1], radius)
            print(f"Area: west {box[0]:.3f}, south {box[1]:.3f}, east {box[2]:.3f}, north {box[3]:.3f} "
                  f"(about {(box[2] - box[0]) * 58:.0f} × {(box[3] - box[1]) * 69:.0f} miles)")
            info = extract(argv[1], radius, dry_run=dry, max_zoom=zoom, geocode=lambda p: _cached(p))
            print(info["output"][-300:] if dry else f"Installed '{info['name']}': {info['bytes'] / 1e6:.0f} MB")
            return 0
        if cmd == "status":
            rs = regions()
            if not rs:
                print('No offline map installed. Run: pergamon maps install "<place or region>"')
                return 1
            for r in rs:
                print(f"  {r['name']:<34} {r.get('bytes', 0) / 1e6:7.0f} MB  zoom ≤{r['max_zoom']}")
            return 0
        if cmd == "remove" and len(argv) > 1:
            print("Removed." if remove(argv[1]) else "No region by that name.")
            return 0
    except (AreaError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


_lookups: dict = {}


def _cached(place: str):
    if place not in _lookups:
        _lookups[place] = lookup(place)
    return _lookups[place]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
