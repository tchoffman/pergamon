"""Tests for /classifieds + /api/classifieds*."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag import classifieds as cl
from rag import identity, nostr
from rag.classifieds import build_listing_event
from rag.nostr import sign_event
from rag.web import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    monkeypatch.setattr(cl, "MINE_PATH", tmp_path / "mine.json")
    monkeypatch.setattr(cl, "publish", lambda ev, relays: {"ws://fake/": (True, "")})
    nsec, _, _ = identity.generate_keypair()
    identity.save_identity("alice", nsec)
    identity.set_active("alice")
    return TestClient(app, raise_server_exceptions=False)


def test_page_renders(client):
    r = client.get("/classifieds")
    assert r.status_code == 200
    assert "Post a listing" in r.text


def test_post_and_list_mine(client):
    r = client.post("/api/classifieds", json={
        "title": "Fresh eggs", "side": "offering", "qty": "3 dozen",
        "price_oz_ag": "0.1", "accepts": "silver, barter", "hashtags": "eggs, #poultry",
    })
    assert r.status_code == 200, r.text
    listing = r.json()["listing"]
    assert listing["title"] == "Fresh eggs"
    assert listing["accepts"] == ["silver", "barter"]
    assert listing["hashtags"] == ["eggs", "poultry"]
    assert listing["is_live"]

    mine = client.get("/api/classifieds/mine").json()
    assert mine["live"] == 1
    assert mine["listings"][0]["d"] == listing["d"]


def test_post_requires_title(client):
    r = client.post("/api/classifieds", json={"title": "  "})
    assert r.status_code == 400


def test_post_rejects_bad_side(client):
    r = client.post("/api/classifieds", json={"title": "x", "side": "maybe"})
    assert r.status_code == 400


def test_soft_cap_returns_409_then_force(client):
    for n in range(cl.SOFT_CAP):
        assert client.post("/api/classifieds", json={"title": f"i{n}"}).status_code == 200
    assert client.post("/api/classifieds", json={"title": "extra"}).status_code == 409
    assert client.post("/api/classifieds", json={"title": "extra", "force": True}).status_code == 200


def test_close_then_renew_unknown(client):
    d = client.post("/api/classifieds", json={"title": "Firewood"}).json()["listing"]["d"]
    r = client.post(f"/api/classifieds/{d}/close")
    assert r.status_code == 200
    assert r.json()["listing"]["status"] == "sold"
    assert client.post("/api/classifieds/nope/renew").status_code == 404


def test_list_endpoint_filters(client, monkeypatch):
    nsec, _, pub = identity.generate_keypair()
    evs = [
        build_listing_event(pub, "Honey", "raw", side="offering"),
        build_listing_event(pub, "Need honey", "", side="looking-for"),
    ]
    for e in evs:
        sign_event(e, nsec)
    monkeypatch.setattr(cl, "fetch_listings", lambda *a, **k: evs)
    data = client.get("/api/classifieds?q=honey&side=looking-for").json()
    assert [l["title"] for l in data["listings"]] == ["Need honey"]
