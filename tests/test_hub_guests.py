"""Guests on the hub's WiFi must never act as the hub's own identity."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag import classifieds, identity, install_config, nostr
from rag.web import app

LAN = ("192.168.1.50", 50000)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    monkeypatch.setattr(classifieds, "MINE_PATH", tmp_path / "mine.json")
    monkeypatch.setattr(classifieds, "publish", lambda ev, relays: {"ws://x/": (True, "")})
    nsec, _, _ = identity.generate_keypair()
    identity.save_identity("host", nsec, display_name="Main St Host")
    identity.set_active("host")
    nsec2, _, _ = identity.generate_keypair()
    identity.save_identity("other", nsec2)
    return TestClient(app, client=LAN), TestClient(app), tmp_path


SIGNING = [
    ("post", "/api/board", {"category": "news", "text": "x"}),
    ("post", "/api/classifieds", {"title": "x"}),
    ("post", "/api/receipts", {"with": "npub1x", "gave": "a", "got": "b"}),
    ("post", "/api/receipts/confirm", {"code": "x"}),
    ("post", "/api/skills", {"title": "x"}),
    ("post", "/api/identity/profile", {"display_name": "pwned"}),
    ("post", "/api/identity/use", {"name": "other"}),
    ("post", "/api/classifieds/abc/renew", None),
    ("post", "/api/classifieds/abc/close", None),
    ("post", "/api/skills/abc/renew", None),
]


def test_guest_cannot_sign_as_hub(setup):
    guest, _, _ = setup
    for method, url, body in SIGNING:
        r = getattr(guest, method)(url, json=body or {})
        assert r.status_code == 403, url
    assert identity.get_active().name == "host"  # couldn't switch it


def test_guest_pages_do_not_offer_hub_identity(setup):
    guest, local, _ = setup
    for page in ("/board", "/classifieds", "/skills", "/receipts", "/identity"):
        assert "Main St Host" not in guest.get(page).text, page
    assert "Main St Host" in local.get("/board").text


def test_guest_reads_need_own_pubkey(setup):
    guest, _, _ = setup
    assert guest.get("/api/classifieds/mine").status_code == 400
    pub = identity.generate_keypair()[2]
    assert guest.get(f"/api/classifieds/mine?pubkey={pub}").status_code == 200


def test_guest_can_still_use_own_key_drafts(setup):
    guest, _, _ = setup
    pub = identity.generate_keypair()[2]
    r = guest.post("/api/draft/board", json={"pubkey": pub, "category": "news", "text": "hi"})
    assert r.status_code == 200


def test_host_on_machine_still_works(setup):
    _, local, _ = setup
    assert local.post("/api/classifieds", json={"title": "Eggs"}).status_code == 200


def test_trust_lan_opt_in(setup):
    guest, _, tmp = setup
    (tmp / "install.local.yaml").write_text("hub:\n  trust_lan: true\n")
    assert guest.post("/api/classifieds", json={"title": "Eggs"}).status_code == 200
    assert "Main St Host" in guest.get("/board").text
