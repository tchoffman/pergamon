"""Pergamon's built-in relay (rag/relay.py), driven through the same client
code the hub uses."""
import asyncio
import json
import socket
import threading
import time

import pytest
import websockets

from rag import board, classifieds, messages, moderation, nostr, relay, sync
from rag.identity import generate_keypair
from rag.nostr import Event, sign_event


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    """The relay on its own event loop in a thread, like `pergamon web` runs it."""

    def __init__(self, db):
        self.port = _free_port()
        self.relay = relay.Relay(db)
        self.loop = asyncio.new_event_loop()
        self._server = None
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._start(ready))
            self.loop.run_forever()
        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)

    async def _start(self, ready):
        from websockets.asyncio.server import serve
        self._server = await serve(self.relay.handler, "127.0.0.1", self.port, max_size=relay.MAX_MESSAGE)
        ready.set()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/"

    def stop(self):
        async def close():
            self._server.close()
            await self._server.wait_closed()
        asyncio.run_coroutine_threadsafe(close(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    r = Running(tmp_path / "events.db")
    yield r
    r.stop()


def keys():
    return generate_keypair()


def test_board_post_round_trip_and_bad_signature(hub):
    nsec, _, pub = keys()
    ev = sign_event(board.build_post(pub, "Ferry running", "news"), nsec)
    assert nostr.publish(ev, [hub.url])[hub.url] == (True, "")
    assert nostr.publish(ev, [hub.url])[hub.url][1].startswith("duplicate")
    assert [p["text"] for p in board.fetch_posts([hub.url])] == ["Ferry running"]
    forged = sign_event(board.build_post(pub, "x", "news"), nsec)
    forged.content = "Bridge is fine"
    ok, msg = nostr.publish(forged, [hub.url])[hub.url]
    assert not ok and "bad signature" in msg


def test_edits_replace_old_versions(hub):
    nsec, _, pub = keys()
    v1 = sign_event(classifieds.build_listing_event(pub, "Bike", "red", listing_id="b1"), nsec)
    nostr.publish(v1, [hub.url])
    time.sleep(1.1)
    v2 = sign_event(classifieds.build_listing_event(pub, "Bike", "red, new tires", listing_id="b1"), nsec)
    nostr.publish(v2, [hub.url])
    ok, msg = nostr.publish(v1, [hub.url])[hub.url]   # an old version arriving late
    assert not ok and "newer version" in msg
    got = classifieds.fetch_listings([hub.url])
    assert [e.content for e in got] == ["red, new tires"] and hub.relay.store.count() == 1
    p1 = sign_event(Event(kind=0, pubkey=pub, content='{"name":"Old"}', created_at=100), nsec)
    p2 = sign_event(Event(kind=0, pubkey=pub, content='{"name":"New"}', created_at=200), nsec)
    nostr.publish(p1, [hub.url]); nostr.publish(p2, [hub.url])
    prof = asyncio.run(nostr.afetch_events([hub.url], {"kinds": [0], "authors": [pub]}))
    assert [e.content for e in prof] == ['{"name":"New"}']


def test_sealed_messages_by_recipient(hub):
    (a_nsec, _, a), (b_nsec, _, b) = keys(), keys()
    for w in messages.build(a_nsec, b, "the gate code is 4711"):
        nostr.publish(w, [hub.url])
    wraps = asyncio.run(nostr.afetch_events([hub.url], {"kinds": [1059], "#p": [b]}))
    assert [messages.open_wrap(b_nsec, w)["text"] for w in wraps] == ["the gate code is 4711"]


def test_filters_since_until_limit_and_ids(hub):
    nsec, _, pub = keys()
    evs = []
    for i, t in enumerate((100, 200, 300)):
        ev = Event(kind=1, pubkey=pub, content=f"n{i}", created_at=int(time.time()) - 1000 + t,
                   tags=[["t", "pergamon-board"]])
        evs.append(sign_event(ev, nsec))
        nostr.publish(evs[-1], [hub.url])
    q = lambda f: [e.content for e in asyncio.run(nostr.afetch_events([hub.url], f))]  # noqa: E731
    base = int(time.time()) - 1000
    assert q({"kinds": [1], "since": base + 150}) == ["n2", "n1"]
    assert q({"kinds": [1], "until": base + 150}) == ["n0"]
    assert q({"kinds": [1], "limit": 1}) == ["n2"]
    assert q({"ids": [evs[0].id]}) == ["n0"]
    assert q({"kinds": [1], "#t": ["other"]}) == []


def test_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    db = tmp_path / "events.db"
    first = Running(db)
    nsec, _, pub = keys()
    nostr.publish(sign_event(board.build_post(pub, "kept", "news"), nsec), [first.url])
    first.stop()
    second = Running(db)
    try:
        assert [p["text"] for p in board.fetch_posts([second.url])] == ["kept"]
    finally:
        second.stop()


def test_live_subscribers_hear_new_posts(hub):
    nsec, _, pub = keys()

    async def listen():
        async with websockets.connect(hub.url) as ws:
            await ws.send(json.dumps(["REQ", "s1", {"kinds": [1]}]))
            assert json.loads(await ws.recv()) == ["EOSE", "s1"]
            ev = sign_event(board.build_post(pub, "just now", "news"), nsec)
            await asyncio.to_thread(nostr.publish, ev, [hub.url])
            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            return msg[0], msg[2]["content"]
    assert asyncio.run(listen()) == ("EVENT", "just now")


def test_moderation_is_enforced_at_the_relay(hub):
    spam_nsec, _, spam = keys()
    nsec, _, pub = keys()
    moderation.block(spam)
    ok, msg = nostr.publish(sign_event(board.build_post(spam, "spam", "news"), spam_nsec), [hub.url])[hub.url]
    assert not ok and "blocked" in msg
    hidden = sign_event(board.build_post(pub, "hide me", "news"), nsec)
    nostr.publish(hidden, [hub.url])
    moderation.hide(hidden.id)
    # Even a client that skips Pergamon's own filter doesn't get it:
    raw = asyncio.run(nostr.subscribe_and_collect(hub.url, {"kinds": [1]}, 10, moderated=False))
    assert raw == []


def test_ephemeral_events_are_not_stored(hub):
    nsec, _, pub = keys()
    ev = sign_event(Event(kind=20001, pubkey=pub, content="typing…"), nsec)
    assert nostr.publish(ev, [hub.url])[hub.url][0] is True
    assert hub.relay.store.count() == 0


def test_two_hubs_gossip_through_built_in_relays(hub, tmp_path):
    other = Running(tmp_path / "other.db")
    try:
        nsec, _, pub = keys()
        nostr.publish(sign_event(board.build_post(pub, "news from town A", "news"), nsec), [hub.url])
        assert sync._copy(hub.url, other.url, since=0) == 0
        assert [p["text"] for p in board.fetch_posts([other.url])] == ["news from town A"]
    finally:
        other.stop()


def test_garbage_gets_a_notice_not_a_crash(hub):
    async def poke():
        async with websockets.connect(hub.url) as ws:
            await ws.send("not json")
            first = json.loads(await ws.recv())
            await ws.send(json.dumps(["EVENT", {"kind": 1}]))
            second = json.loads(await ws.recv())
            return first[0], second[0]
    assert asyncio.run(poke()) == ("NOTICE", "NOTICE")
