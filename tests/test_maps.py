"""Offline map: install pieces, and serving the map to phones."""
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from rag import maps, web


def test_page_explains_when_no_map_is_installed():
    c = TestClient(web.app)
    html = c.get("/maps").text
    assert "No offline map is installed" in html and "pergamon maps install" in html
    assert c.get("/maps/regions/north-texas.pmtiles").status_code == 404


def _install_fake_map():
    root = maps.maps_dir()
    (root / "web" / "fonts" / "Noto Sans Regular").mkdir(parents=True)
    (root / "web" / "maplibre-gl.js").write_text("// viewer")
    (root / "web" / "fonts" / "Noto Sans Regular" / "0-255.pbf").write_bytes(b"glyphs")
    for name, bbox, size in (("North Texas", [-98.1, 32.0, -95.8, 33.5], 2048),
                             ("Atlanta Metro", [-85.1, 33.2, -83.8, 34.4], 1024)):
        maps.tiles_path(name).parent.mkdir(parents=True, exist_ok=True)
        maps.tiles_path(name).write_bytes(bytes(range(256)) * 4)
        maps.info_path(name).write_text(json.dumps({"name": name, "area": name, "radius_mi": None,
                                                    "max_zoom": 15, "bbox": bbox, "bytes": size}))


def test_installed_map_is_served_in_byte_ranges():
    _install_fake_map()
    c = TestClient(web.app)
    html = c.get("/maps?region=atlanta-metro").text
    assert 'id="map"' in html and "/maps/regions/atlanta-metro.pmtiles" in html
    assert 'value="north-texas"' in html  # the picker lists every region
    r = c.get("/maps/regions/north-texas.pmtiles", headers={"Range": "bytes=16-31"})
    assert r.status_code == 206 and r.content == bytes(range(16, 32))
    assert c.get("/maps/web/fonts/Noto Sans Regular/0-255.pbf").content == b"glyphs"


def test_viewer_files_cannot_escape_their_folder():
    _install_fake_map()
    c = TestClient(web.app)
    assert c.get("/maps/web/../regions/north-texas.json").status_code == 404
    assert c.get("/maps/web/%2e%2e/regions/north-texas.json").status_code == 404
    assert c.get("/maps/regions/..%2Fweb%2Fmaplibre-gl.js.pmtiles").status_code == 404
    assert c.get("/maps/web/nope.js").status_code == 404


def test_pmtiles_tool_must_match_its_published_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(maps.shutil, "which", lambda name: None)
    monkeypatch.setattr(maps.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(maps.platform, "machine", lambda: "arm64")
    with pytest.raises(RuntimeError, match="published checksum"):
        maps.pmtiles_bin(get=lambda url: b"something else")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pmtiles", b"#!binary")
    blob = buf.getvalue()
    name, _ = maps.PMTILES_BUILDS[("Darwin", "arm64")]
    monkeypatch.setitem(maps.PMTILES_BUILDS, ("Darwin", "arm64"), (name, hashlib.sha256(blob).hexdigest()))
    path = maps.pmtiles_bin(get=lambda url: blob)
    assert path.read_bytes() == b"#!binary" and path.stat().st_mode & 0o100


def test_extract_cuts_the_region_and_records_it(monkeypatch):
    monkeypatch.setattr(maps, "pmtiles_bin", lambda: "pmtiles")
    monkeypatch.setattr(maps, "latest_build", lambda: "https://build.protomaps.com/20261120.pmtiles")
    seen = {}

    def run(cmd, capture_output, text):
        seen["cmd"] = cmd
        maps.tiles_path("Keller, TX (25 mi)").parent.mkdir(parents=True, exist_ok=True)
        maps.tiles_path("Keller, TX (25 mi)").write_bytes(b"x" * 2048)

        class R:
            returncode, stdout, stderr = 0, "", "extracted 1234 tiles"
        return R()
    seen_out = {}
    real_run = run

    def run2(cmd, capture_output, text):
        seen_out["path"] = cmd[3]
        maps.tiles_path("Keller, TX (25 mi)").parent.mkdir(parents=True, exist_ok=True)
        return real_run(cmd, capture_output, text)
    info = maps.extract("Keller, TX", 25, run=run2, geocode=lambda a: (32.93, -97.23, None))
    assert seen["cmd"][1:3] == ["extract", "https://build.protomaps.com/20261120.pmtiles"]
    assert any(a.startswith("--bbox=") for a in seen["cmd"]) and "--maxzoom=15" in seen["cmd"]
    assert info["name"] == "Keller, TX (25 mi)"



def test_the_ways_to_describe_a_region():
    places = {"Keller, TX": (32.93, -97.22, [32.89, 32.98, -97.27, -97.18]),
              "Tarrant County, Texas": (32.77, -97.29, [32.55, 33.0, -97.55, -97.03]),
              "Dallas County, Texas": (32.77, -96.78, [32.55, 33.02, -97.04, -96.52]),
              "Pioneer Park": (32.95, -97.23, None)}
    look = lambda p: places[p]  # noqa: E731
    assert maps.region_bbox("-97.6,32.5,-96.5,33.4") == (-97.6, 32.5, -96.5, 33.4)
    w, s, e, n = maps.region_bbox("32.93,-97.23", 10)
    assert round(n - s, 2) == round(20 / 69, 2)
    w, s, e, n = maps.region_bbox("Keller, TX", geocode=look)          # the town's own boundary
    assert w < -97.27 and e > -97.18 and s < 32.89 and n > 32.98
    w, s, e, n = maps.region_bbox("Keller, TX", 25, geocode=look)      # a radius instead
    assert round(n - s, 1) == round(50 / 69, 1)
    w, s, e, n = maps.region_bbox("Tarrant County, Texas; Dallas County, Texas", geocode=look)
    assert w < -97.55 and e > -96.52                                   # both counties
    w, s, e, n = maps.region_bbox("Pioneer Park", geocode=look)        # a point: 10 miles around
    assert round(n - s, 1) == round(20 / 69, 1)
    with pytest.raises(maps.AreaError):
        maps.region_bbox("-97,33,-98,32")                              # west must be less than east


def test_named_regions_use_their_counties():
    seen = []

    def look(p):
        seen.append(p)
        return (33.0, -97.0, [32.5, 33.5, -97.5, -96.5])
    maps.region_bbox("north texas", geocode=look)
    assert "Tarrant County, Texas" in seen and "Wise County, Texas" in seen
    assert "Cobb County, Georgia" in maps.named_regions()["atlanta metro"]["places"]
