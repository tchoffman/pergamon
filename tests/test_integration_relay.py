"""End-to-end over real WebSockets against an in-process relay."""
from __future__ import annotations

import asyncio

import pytest

from rag import board, classifieds, nostr, sync
from rag.identity import generate_keypair
from rag.nostr import publish, sign_event
from tests.mini_relay import MiniRelay


@pytest.fixture
def relays():
    a, b = MiniRelay().start(), MiniRelay().start()
    yield a, b
    a.stop()
    b.stop()


def _who():
    nsec, _, pub = generate_keypair()

    class Ident:
        hex_pub = pub

    return Ident, nsec, pub


def test_board_post_and_fetch(relays):
    a, _ = relays
    _, nsec, pub = _who()
    ev = sign_event(board.build_post(pub, "Well water tested clean", "news"), nsec)
    assert publish(ev, [a.url])[a.url][0] is True
    posts = board.fetch_posts([a.url])
    assert [p["text"] for p in posts] == ["Well water tested clean"]
    assert board.fetch_posts([a.url], category="event") == []


def test_classified_renew_supersedes(relays, tmp_path, monkeypatch):
    a, _ = relays
    monkeypatch.setattr(classifieds, "MINE_PATH", tmp_path / "mine.json")
    monkeypatch.setattr(classifieds, "_load_relays_from_config", lambda: [a.url])
    ident, nsec, _ = _who()
    ev, res = classifieds.post_listing(ident, nsec, title="Honey", description="raw",
                                       listing_id="honey-1")
    assert res[a.url][0]
    closed = classifieds.republish("honey-1", nsec, status="sold")
    closed.created_at = ev.created_at + 1  # same-second post; force ordering
    sign_event(closed, nsec)
    publish(closed, [a.url])
    assert classifieds.fetch_listings([a.url]) == []
    all_versions = classifieds.fetch_listings([a.url], live_only=False)
    assert len(all_versions) == 1
    assert classifieds.summarize(all_versions[0])["status"] == "sold"


def test_gossip_between_two_relays(relays):
    a, b = relays
    _, nsec, pub = _who()
    evs = [
        sign_event(board.build_post(pub, "Potluck Saturday", "event"), nsec),
        sign_event(classifieds.build_listing_event(pub, "Firewood", "oak"), nsec),
    ]
    for e in evs:
        publish(e, [a.url])
    # An unrelated kind-1 note without the board tag must not travel.
    stray = sign_event(nostr.build_event(1, "just a note", pub), nsec)
    publish(stray, [a.url])

    assert sync._copy(a.url, b.url, since=0) == 0
    assert set(b.events) == {e.id for e in evs}
    # Second sync is a no-op that still succeeds (duplicates count as stored).
    assert sync._copy(a.url, b.url, since=0) == 0


def test_sneakernet_bundle_between_towns(relays, tmp_path):
    a, b = relays
    _, nsec, pub = _who()
    ev = sign_event(board.build_post(pub, "Clinic open Tuesdays", "notice"), nsec)
    publish(ev, [a.url])
    bundle = tmp_path / "usb.jsonl"
    sync.write_bundle(asyncio.run(sync.collect(a.url, since=0)), bundle)
    events, bad = sync.read_bundle(bundle)
    assert bad == 0
    ok, rej, errs = asyncio.run(sync.publish_batch(b.url, events))
    assert (ok, rej, errs) == (1, 0, [])
    assert [p["text"] for p in board.fetch_posts([b.url])] == ["Clinic open Tuesdays"]
