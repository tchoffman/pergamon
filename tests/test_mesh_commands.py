"""Radio commands against a real in-process relay."""
from __future__ import annotations

import pytest

from modules.comms.meshtastic import commands
from rag import board, classifieds, skills
from rag.identity import generate_keypair
from rag.nostr import build_event, publish, sign_event
from tests.mini_relay import MiniRelay


@pytest.fixture
def relay():
    r = MiniRelay().start()
    yield r
    r.stop()


def _key():
    nsec, _, pub = generate_keypair()
    return nsec, pub


def _pub(ev, nsec, relay):
    assert publish(sign_event(ev, nsec), [relay.url])[relay.url][0]


def test_questions_are_not_commands():
    assert commands.run("find water in the desert?", relays=[]) is None
    assert commands.run("How do I purify water?", relays=[]) is None


def test_ping_help_unknown():
    assert commands.run("ping", relays=[]) == "pong"
    assert commands.run("help", relays=[]) == commands.HELP
    assert commands.run("/bogus", relays=[]).startswith("Unknown command /bogus")


def test_news_shows_latest_posts_with_author_name(relay):
    nsec, pub = _key()
    _pub(build_event(kind=0, content='{"name": "Ada"}', pubkey_hex=pub, tags=[]), nsec, relay)
    _pub(board.build_post(pub, "Well water tested clean at the church", "news"), nsec, relay)
    out = commands.run("/news", relays=[relay.url])
    assert "Ada" in out and "Well water tested clean" in out


def test_news_empty_and_bad_category(relay):
    assert commands.run("/news", relays=[relay.url]) == "Board is empty."
    assert "No board category" in commands.run("/news gossip", relays=[relay.url])


def test_find_lists_live_classifieds(relay):
    nsec, pub = _key()
    ev = classifieds.build_listing_event(pub, title="Fresh eggs", description="a dozen",
                                         listing_id="eggs-123456", location="Elm St")
    _pub(ev, nsec, relay)
    out = commands.run("/find eggs", relays=[relay.url])
    assert "Fresh eggs" in out and "Elm St" in out and "[eggs-1]" in out
    assert commands.run("/find tractor", relays=[relay.url]) == "No live listings for 'tractor'."
    assert commands.run("/find", relays=[relay.url]).startswith("Usage")


def test_skills_search(relay):
    nsec, pub = _key()
    _pub(skills.build_skill_event(pub, "Welding and metal repair", "MIG and stick"), nsec, relay)
    out = commands.run("/skills welding", relays=[relay.url])
    assert "Welding and metal repair" in out


def test_replies_fit_a_few_radio_packets(relay):
    nsec, pub = _key()
    for i in range(6):
        _pub(board.build_post(pub, "x" * 400 + f" post {i}", "news"), nsec, relay)
    out = commands.run("/news", relays=[relay.url])
    assert len(out) < 600 and "(+3 more)" in out


def test_offline_relay_is_reported_not_empty():
    assert commands.run("/news", relays=["ws://127.0.0.1:1/"]) == commands.OFFLINE
