from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag import board, classifieds, identity, nostr
from rag.board import BOARD_TAG, build_post, summarize
from rag.classifieds import build_listing_event
from rag.nostr import sign_event, verify_event
from rag.web import app


def _pub():
    return identity.generate_keypair()[2]


def test_build_post_tags():
    ev = build_post(_pub(), "Bridge out on Rt 9", "news", location="Rt 9")
    tags = [tuple(t) for t in ev.tags]
    assert ("t", BOARD_TAG) in tags
    assert ("t", f"{BOARD_TAG}-news") in tags
    assert ("category", "news") in tags
    assert ("location", "Rt 9") in tags
    assert ev.kind == 1


def test_build_post_validation():
    with pytest.raises(ValueError):
        build_post(_pub(), "x", "gossip")
    with pytest.raises(ValueError):
        build_post(_pub(), "   ", "news")
    with pytest.raises(ValueError):
        build_post(_pub(), "x" * (board.MAX_LEN + 1), "news")


def test_summarize_and_sign():
    nsec, _, pub = identity.generate_keypair()
    ev = sign_event(build_post(pub, "Potluck Sat 5pm", "event"), nsec)
    assert verify_event(ev)
    s = summarize(ev)
    assert s["category"] == "event"
    assert s["category_title"] == "Events"
    assert s["text"] == "Potluck Sat 5pm"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    monkeypatch.setattr(board, "publish", lambda ev, relays: {"ws://fake/": (True, "")})
    nsec, _, _ = identity.generate_keypair()
    identity.save_identity("host", nsec, display_name="Main St Host")
    identity.set_active("host")
    return TestClient(app, raise_server_exceptions=False)


def test_board_page_and_post(client):
    assert client.get("/board").status_code == 200
    r = client.post("/api/board", json={"category": "lost-found", "text": "Found: brown dog"})
    assert r.status_code == 200
    assert r.json()["post"]["category"] == "lost-found"
    assert client.post("/api/board", json={"category": "nope", "text": "x"}).status_code == 400


def test_bulletin_renders_listings_and_posts(client, monkeypatch):
    nsec, _, pub = identity.generate_keypair()
    offer = sign_event(build_listing_event(pub, "Fresh eggs", "", qty="3 dozen"), nsec)
    want = sign_event(build_listing_event(pub, "Need firewood", "", side="looking-for"), nsec)
    monkeypatch.setattr(classifieds, "fetch_listings", lambda *a, **k: [offer, want])
    post = summarize(sign_event(build_post(pub, "Road closed at bridge", "news"), nsec))
    monkeypatch.setattr(board, "fetch_posts", lambda *a, **k: [post])
    from rag import skills

    async def _skills(*a, **k):
        return [sign_event(skills.build_skill_event(pub, "Midwife", "", category="medical"), nsec)]

    monkeypatch.setattr(skills, "afetch", _skills)
    r = client.get("/bulletin")
    assert r.status_code == 200
    html = r.text
    assert "Fresh eggs" in html and "3 dozen" in html
    assert "Need firewood" in html
    assert "Road closed at bridge" in html
    assert "Main St Host" in html
    assert "Midwife" in html
