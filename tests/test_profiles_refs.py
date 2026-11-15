from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from rag import board, classifieds, identity, nostr, profiles, skills
from rag.identity import generate_keypair
from rag.nostr import build_event, publish, sign_event
from tests.mini_relay import MiniRelay


def _profile(nsec, pub, name, ts=None):
    ev = build_event(0, json.dumps({"display_name": name}), pub)
    if ts:
        ev.created_at = ts
    return sign_event(ev, nsec)


def test_names_latest_profile_wins():
    nsec, _, pub = generate_keypair()
    old = _profile(nsec, pub, "Old Name", ts=1000)
    new = _profile(nsec, pub, "Rosa", ts=2000)
    assert profiles.names_from_events([new, old]) == {pub: "Rosa"}
    assert profiles.label({}, pub) == pub[:8] + "…"


def test_bad_profile_ignored():
    nsec, _, pub = generate_keypair()
    ev = sign_event(build_event(0, "not json", pub), nsec)
    assert profiles.names_from_events([ev]) == {}


def test_resolve_ref_prefix_and_ambiguity(tmp_path, monkeypatch):
    monkeypatch.setattr(classifieds, "MINE_PATH", tmp_path / "m.json")
    nsec, _, pub = generate_keypair()
    for d in ("abc123ffff", "abc999eeee", "zzz000"):
        ev = sign_event(classifieds.build_listing_event(pub, d, "", listing_id=d), nsec)
        classifieds._remember(ev)
    assert classifieds.resolve_ref("abc123") == "abc123ffff"
    assert classifieds.resolve_ref("ZZZ000") == "zzz000"
    with pytest.raises(KeyError, match="matches 2"):
        classifieds.resolve_ref("abc")
    with pytest.raises(KeyError):
        classifieds.resolve_ref("nope")
    other = generate_keypair()[2]
    with pytest.raises(KeyError):
        classifieds.resolve_ref("abc123", other)
    renewed = classifieds.republish("abc123", nsec)
    assert classifieds.summarize(renewed)["d"] == "abc123ffff"


def test_bulletin_shows_names_short_refs_clean_prices(tmp_path, monkeypatch):
    relay = MiniRelay().start()
    try:
        monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
        monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
        nostr._save_relays([relay.url])
        nsec, _, pub = generate_keypair()
        publish(_profile(nsec, pub, "Rosa"), [relay.url])
        ev = sign_event(classifieds.build_listing_event(pub, "Firewood", "", price_usd=90.0,
                                                        price_oz_ag=0.5, listing_id="abcdef123456"), nsec)
        publish(ev, [relay.url])
        publish(sign_event(board.build_post(pub, "Road open", "news"), nsec), [relay.url])
        html = TestClient(app_()).get("/bulletin").text
        assert "from Rosa" in html
        assert "<b>abcdef</b>" in html and "abcdef123456" not in html
        assert "$90 " in html or "$90<" in html or "$90 ·" in html
        assert "0.5 oz Ag" in html
        api = TestClient(app_()).get("/api/classifieds").json()["listings"][0]
        assert api["author"] == "Rosa" and api["ref"] == "abcdef"
    finally:
        relay.stop()


def app_():
    from rag.web import app
    return app
