import io
import json
import subprocess
import urllib.error

import pytest

from rag import install_test, model_check, osm_regions, service_check


# ── planning the "everything" sweep ──

CATALOG = {
    "wiki": {"id": "wiki", "fetcher": "kiwix-zim",
             "kiwix": {"prefix_by_variant": {"a": "w_a_", "b": "w_b_", "c": "w_c_"}}},
    "pdf": {"id": "pdf", "fetcher": "archive-pdf"},
    "se": {"id": "se", "fetcher": "stackexchange", "defaults": {"sites": ["x"]},
           "options": [{"name": "sites", "choices": ["x", "y", "z"]}]},
    "osm-regional": {"id": "osm-regional", "fetcher": "osmand-region"},
}
MODELS = {"primary": {"p1": {}, "p2": {}}, "embedding": {"e1": {}}}


def test_rounds_cover_every_variant_model_and_service_once():
    rs = install_test.rounds(CATALOG, MODELS, {"radicale": {}, "relay": {}})
    assert len(rs) == 3  # the item with the most variants
    assert [r["overrides"]["wiki"] for r in rs] == [{"variant": v} for v in "abc"]
    assert "pdf" in rs[0]["overrides"] and "pdf" not in rs[1]["overrides"]
    assert rs[0]["overrides"]["se"] == {"sites": ["x", "y", "z"]}  # multi-select: all at once
    assert rs[0]["overrides"]["osm-regional"]["regions"]  # free text gets a sample value
    assert {r["models"]["primary"] for r in rs} == {"p1", "p2"}
    assert rs[0]["services"] == {"radicale": {}, "relay": {}} and rs[1]["services"] == {}
    assert all(r["preset"] == install_test.EMPTY_PRESET for r in rs)


def test_rounds_for_the_real_catalog_are_well_formed():
    from rag.install_config import load_catalog
    rs = install_test.rounds()
    covered = set().union(*(r["overrides"] for r in rs))
    assert covered == set(load_catalog())


def test_install_config_follows_the_sandbox(tmp_path):
    out = subprocess.run(
        ["build/venv/bin/python", "-c",
         "from rag import install_config as i, config as c; print(i.INSTALL_LOCAL_PATH); "
         "print(c.load_config()['chroma_dir'])"],
        env={"PERGAMON_SANDBOX": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True).stdout.split()
    assert out == [str(tmp_path / "install.local.yaml"), str(tmp_path / "chroma")]


# ── models and images, checked without downloading ──

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_model_check_reports_size_or_missing():
    assert model_check.manifest_url("qwen3.5:9b").endswith("/library/qwen3.5/manifests/9b")
    assert model_check.manifest_url("nomic-embed-text").endswith("/manifests/latest")
    ok = lambda req, timeout: FakeResponse(json.dumps({"layers": [{"size": 3e9}, {"size": 5e8}]}).encode())
    assert model_check.check("x:1", opener=ok) == (True, "3.5 GB download")

    def missing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    assert model_check.check("x:1", opener=missing) == (False, "not in the registry (HTTP 404)")


@pytest.mark.parametrize("ref,expected", [
    ("vaultwarden/server:latest", ("registry-1.docker.io", "vaultwarden/server", "latest")),
    ("alpine", ("registry-1.docker.io", "library/alpine", "latest")),
    ("lscr.io/linuxserver/calibre-web:latest", ("lscr.io", "linuxserver/calibre-web", "latest")),
    ("localhost:5000/x", ("localhost:5000", "x", "latest")),
])
def test_image_refs_parse_like_docker(ref, expected):
    assert service_check.parse_image(ref) == expected


def test_image_check_does_the_anonymous_token_dance():
    calls = []

    def opener(req, timeout):
        calls.append((req.full_url, req.headers.get("Authorization")))
        if "token" in req.full_url:
            return FakeResponse(b'{"token": "T"}')
        if req.headers.get("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(req.full_url, 401, "auth", {
                "WWW-Authenticate": 'Bearer realm="https://auth.example/token",service="reg",'
                                    'scope="repository:a/b:pull"'}, None)
        return FakeResponse(b"{}")
    assert service_check.check_image("a/b:1", opener=opener) == (True, "image found")
    assert "service=reg" in calls[1][0] and calls[2][1] == "Bearer T"


def test_every_service_has_an_image():
    from rag.install_config import load_services_catalog
    for sid in load_services_catalog():
        assert service_check.images_for(sid), sid


# ── map regions: what people type → OsmAnd's file names ──

FILES = ["Us_georgia_northamerica_2.obf.zip", "Us_north-carolina_northamerica_2.obf.zip",
         "Germany_berlin_europe_2.obf.zip", "Gb_england_greater-london_europe_2.obf.zip",
         "Gb_england_east-of-england_europe_2.obf.zip"]


@pytest.mark.parametrize("typed,file", [
    ("us-georgia", "Us_georgia_northamerica_2.obf.zip"),
    ("us-northcarolina", "Us_north-carolina_northamerica_2.obf.zip"),
    ("US North Carolina", "Us_north-carolina_northamerica_2.obf.zip"),
    ("germany-berlin", "Germany_berlin_europe_2.obf.zip"),
    ("Us_georgia_northamerica_2.obf.zip", "Us_georgia_northamerica_2.obf.zip"),
])
def test_region_names_resolve(typed, file):
    assert osm_regions.resolve(typed, FILES) == (file, [])


def test_ambiguous_or_unknown_regions_explain():
    name, others = osm_regions.resolve("gb-england", FILES)
    assert name is None and len(others) == 2
    assert osm_regions.resolve("atlantis", FILES) == (None, [])


def test_map_files_reads_only_map_entries():
    xml = ('<region type="map" size="1" name="Us_georgia_northamerica_2.obf.zip"/>'
           '<region type="voice" name="en_0.voice.zip"/>')
    assert osm_regions.map_files(xml) == ["Us_georgia_northamerica_2.obf.zip"]
