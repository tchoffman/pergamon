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
    assert c.get("/maps/region.pmtiles").status_code == 404


def _install_fake_map():
    root = maps.maps_dir()
    (root / "web" / "fonts" / "Noto Sans Regular").mkdir(parents=True)
    (root / "web" / "maplibre-gl.js").write_text("// viewer")
    (root / "web" / "fonts" / "Noto Sans Regular" / "0-255.pbf").write_bytes(b"glyphs")
    maps.tiles_path().write_bytes(bytes(range(256)) * 4)
    maps.info_path().write_text(json.dumps({"area": "Keller, TX", "radius_mi": 25, "max_zoom": 15,
                                            "bbox": [-97.6, 32.6, -96.8, 33.3], "bytes": 1024}))


def test_installed_map_is_served_in_byte_ranges():
    _install_fake_map()
    c = TestClient(web.app)
    html = c.get("/maps").text
    assert 'id="map"' in html and "/maps/region.pmtiles" in html and "25 miles around Keller, TX" in html
    r = c.get("/maps/region.pmtiles", headers={"Range": "bytes=16-31"})
    assert r.status_code == 206 and r.content == bytes(range(16, 32))
    assert c.get("/maps/web/fonts/Noto Sans Regular/0-255.pbf").content == b"glyphs"


def test_viewer_files_cannot_escape_their_folder():
    _install_fake_map()
    c = TestClient(web.app)
    assert c.get("/maps/web/../region.json").status_code == 404
    assert c.get("/maps/web/%2e%2e/region.json").status_code == 404
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
        maps.tiles_path().write_bytes(b"x" * 2048)

        class R:
            returncode, stdout, stderr = 0, "", "extracted 1234 tiles"
        return R()
    info = maps.extract("Keller, TX", 25, run=run, geocode=lambda a: (32.93, -97.23))
    assert seen["cmd"][1:3] == ["extract", "https://build.protomaps.com/20261120.pmtiles"]
    assert any(a.startswith("--bbox=") for a in seen["cmd"]) and "--maxzoom=15" in seen["cmd"]
    assert info["bytes"] == 2048 and maps.region_info()["area"] == "Keller, TX"
