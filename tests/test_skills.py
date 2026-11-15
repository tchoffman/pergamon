from __future__ import annotations

import asyncio

import pytest

from rag import skills
from rag.identity import generate_keypair
from rag.nostr import publish, sign_event, verify_event
from tests.mini_relay import MiniRelay


def _kp():
    return generate_keypair()


def test_build_and_summarize():
    nsec, _, pub = _kp()
    ev = sign_event(skills.build_skill_event(
        pub, "Small engine repair", "Mowers, generators", category="repair",
        level="journeyman", availability="weekends", radius_mi=15,
        accepts=["barter", "silver"], cert="none"), nsec)
    assert verify_event(ev) and ev.kind == skills.KIND_SKILL
    s = skills.summarize(ev)
    assert s["category"] == "repair" and s["level"] == "journeyman"
    assert s["radius_mi"] == 15 and s["accepts"] == ["barter", "silver"]
    assert s["is_live"]


def test_validation():
    pub = _kp()[2]
    with pytest.raises(ValueError):
        skills.build_skill_event(pub, "x", "", category="wizardry")
    with pytest.raises(ValueError):
        skills.build_skill_event(pub, "x", "", level="grandmaster")
    with pytest.raises(ValueError):
        skills.build_skill_event(pub, "  ", "")


def test_search_filters_and_expiry():
    nsec, _, pub = _kp()
    evs = [
        sign_event(skills.build_skill_event(pub, "Midwife", "home births", category="medical"), nsec),
        sign_event(skills.build_skill_event(pub, "Welding", "MIG + stick", category="metalwork"), nsec),
        sign_event(skills.build_skill_event(pub, "Old gig", "", ttl_days=-1), nsec),
    ]
    assert [h["title"] for h in skills.search(evs, "birth")] == ["Midwife"]
    assert [h["title"] for h in skills.search(evs, category="metalwork")] == ["Welding"]
    assert "Old gig" not in {h["title"] for h in skills.search(evs)}


def test_republish_close(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "MINE_PATH", tmp_path / "mine.json")
    nsec, _, pub = _kp()
    ev = sign_event(skills.build_skill_event(pub, "Tutoring", "", listing_id="t1",
                                             category="teaching"), nsec)
    skills._remember(ev)
    closed = skills.republish("t1", nsec, status="closed")
    assert skills.summarize(closed)["status"] == "closed"
    assert skills.search([ev, closed]) == []  # newest version wins and it's closed


def test_find_over_relay():
    relay = MiniRelay().start()
    try:
        nsec, _, pub = _kp()
        publish(sign_event(skills.build_skill_event(pub, "Ham radio license classes", "",
                                                    category="comms"), nsec), [relay.url])
        events = asyncio.run(skills.afetch([relay.url], "comms"))
        assert [h["title"] for h in skills.search(events, "ham")] == ["Ham radio license classes"]
        assert asyncio.run(skills.afetch([relay.url], "medical")) == []
    finally:
        relay.stop()
