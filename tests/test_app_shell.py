import json
import re
import struct

import pytest
from fastapi.testclient import TestClient

from rag import web


@pytest.fixture
def client():
    return TestClient(web.app)


def test_home_lists_every_app_and_each_app_links_home(client):
    home = client.get("/").text
    hrefs = re.findall(r'class="app" href="([^"]+)"', home)
    assert hrefs == [h for _, h, _, _ in web.APPS]  # this machine is the host: all shown
    for href in hrefs:
        r = client.get(href)
        assert r.status_code == 200, href
        assert 'href="/"' in r.text, f"{href} has no way back home"


def test_household_app_hidden_from_guests(client, monkeypatch):
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    monkeypatch.setattr(web, "_local_cfg", lambda: {})
    assert 'href="/food"' not in client.get("/").text


def test_welcome_is_home_plus_introduction(client):
    html = client.get("/welcome").text
    assert 'class="app" href="/chat"' in html
    assert 'id="dk_create"' in html  # key for posting, as before
    assert "dk_create" not in client.get("/").text


def test_pages_are_installable(client):
    for page in ("/", "/chat", "/board", "/library"):
        html = client.get(page).text
        assert '<link rel="manifest" href="/manifest.webmanifest"' in html, page
        assert 'rel="apple-touch-icon"' in html, page
        assert "/static/shell.js" in html, page
    m = client.get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    data = m.json()
    assert data["display"] == "standalone" and data["start_url"] == "/"
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i["purpose"] == "maskable" for i in data["icons"])
    for icon in data["icons"]:
        png = client.get(icon["src"])
        assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", png.content[16:24])
        assert f"{w}x{h}" == icon["sizes"]


def test_service_worker_precaches_static_files_and_offline_page(client):
    r = client.get("/sw.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert "__VERSION__" not in r.text and "__PRECACHE__" not in r.text
    precache = json.loads(re.search(r"const PRECACHE = (\[.*?\]);", r.text).group(1))
    assert "/offline" in precache and "/static/style.css" in precache
    for url in precache:
        assert client.get(url).status_code == 200, url
    assert client.get("/offline").status_code == 200


def test_service_worker_version_follows_static_files(client, tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "sw.js").write_text('const VERSION = "__VERSION__"; const PRECACHE = __PRECACHE__;')
    (static / "style.css").write_text("a{}")
    monkeypatch.setattr(web, "STATIC_DIR", static)
    v1 = web._sw_source()
    (static / "style.css").write_text("a{color:red}")
    assert web._sw_source() != v1


def test_service_worker_never_touches_answers_or_posts():
    src = (web.STATIC_DIR / "sw.js").read_text()
    assert 'req.method !== "GET"' in src
    # only static files are served from cache; pages fall back to /offline
    assert 'url.pathname.startsWith("/static/")' in src and 'req.mode === "navigate"' in src


def test_secure_setup_not_offered_on_the_hub_itself(client, monkeypatch):
    monkeypatch.setattr(web, "_https_settings", lambda: (True, 8443))
    assert "Set up a secure connection" in client.get("http://192.168.1.20/").text
    assert "Set up a secure connection" not in client.get("http://localhost/").text
