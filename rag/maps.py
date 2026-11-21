"""Offline street map of the hub's region.

At install, a region (a place and a radius) is cut out of the free worldwide
Protomaps map with the `pmtiles` tool, which downloads only the tiles inside
it. The hub serves that one file plus the map viewer (MapLibre), its fonts and
icons, so phones on the hub's Wi-Fi get a normal pan-and-zoom map with no
internet.

    pergamon maps install "Keller, TX" 25     # region around a place, radius in miles
    pergamon maps status
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


def tiles_path() -> Path:
    return maps_dir() / "region.pmtiles"


def web_dir() -> Path:
    return maps_dir() / "web"


def info_path() -> Path:
    return maps_dir() / "region.json"


def region_info() -> dict | None:
    try:
        return json.loads(info_path().read_text())
    except (OSError, ValueError):
        return None


def installed() -> bool:
    return tiles_path().exists() and (web_dir() / "maplibre-gl.js").exists()


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

def region_bbox(area: str, radius_mi: float, geocode=None) -> tuple[float, float, float, float]:
    from rag import usgs_topo
    lat, lon = (geocode or usgs_topo.locate)(area)
    return usgs_topo.bbox(lat, lon, radius_mi)


def extract(area: str, radius_mi: float, dry_run: bool = False, max_zoom: int = MAX_ZOOM,
            run=subprocess.run, geocode=None) -> dict:
    """Cut the region out of the latest worldwide build into region.pmtiles."""
    box = region_bbox(area, radius_mi, geocode)
    src = latest_build()
    out = tiles_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(pmtiles_bin()), "extract", src, str(out), "--bbox=" + ",".join(f"{v:.5f}" for v in box),
           f"--maxzoom={max_zoom}"]
    if dry_run:
        cmd.append("--dry-run")
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "pmtiles extract failed")
    info = {"area": area, "radius_mi": radius_mi, "bbox": box, "max_zoom": max_zoom,
            "source": src, "output": (r.stdout + r.stderr).strip()[-400:]}
    if not dry_run:
        info["bytes"] = out.stat().st_size
        info_path().write_text(json.dumps(info, indent=1))
    return info


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
    if cmd == "install" and len(argv) >= 2:
        radius = float(argv[2]) if len(argv) > 2 else 25
        print(f"Fetching the map viewer, fonts and icons…")
        print(f"  {fetch_web()} file(s) downloaded")
        print(f"Cutting a {radius:g}-mile region around {argv[1]} out of the worldwide map…")
        info = extract(argv[1], radius)
        print(f"  done: {info['bytes'] / 1e6:.0f} MB in {tiles_path()}")
        return 0
    if cmd == "status":
        info = region_info()
        if not installed() or not info:
            print("No offline map installed. Run: pergamon maps install \"<place>\" <miles>")
            return 1
        print(f"Offline map: {info['radius_mi']:g} miles around {info['area']}, "
              f"{info.get('bytes', 0) / 1e6:.0f} MB, zoom up to {info['max_zoom']}.")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
