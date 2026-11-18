"""Host moderation: hidden posts and blocked keys disappear everywhere on this
hub (pages, radio commands, sync) and nowhere else."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from rag import board, classifieds, moderation, nostr, sync, web
from rag.identity import generate_keypair
from rag.nostr import sign_event
from tests.mini_relay import MiniRelay


@pytest.fixture
def hub(tmp_path, monkeypatch):
    relay = MiniRelay().start()
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([relay.url])
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    yield relay
    relay.stop()


def _post(relay, text, keys=None):
    nsec, _, pub = keys or generate_keypair()
    ev = sign_event(board.build_post(pub, text, "news"), nsec)
    nostr.publish(ev, [relay.url])
    return ev, (nsec, None, pub)


def _board_texts(client):
    return [p["text"] for p in client.get("/api/board").json()["posts"]]


def test_hidden_post_leaves_every_page_and_radio_command(hub):
    from modules.comms.meshtastic import commands
    spam, _ = _post(hub, "BUY CHEAP WATCHES")
    _post(hub, "Ferry running today")
    c = TestClient(web.app)
    assert "BUY CHEAP WATCHES" in _board_texts(c)
    assert "WATCHES" in (commands.run("/news", [hub.url]) or "")
    assert c.post("/api/moderation/hide", json={"id": spam.id, "kind": 1, "pubkey": spam.pubkey,
                                                "preview": spam.content}).status_code == 200
    assert _board_texts(c) == ["Ferry running today"]
    assert "WATCHES" not in (commands.run("/news", [hub.url]) or "")
    assert "BUY CHEAP WATCHES" in c.get("/moderation").text  # listed for undo
    assert c.post("/api/moderation/unhide", json={"key": spam.id}).json() == {"unhidden": True}
    assert "BUY CHEAP WATCHES" in _board_texts(c)


def test_edited_listing_stays_hidden(hub):
    nsec, _, pub = generate_keypair()
    ev = sign_event(classifieds.build_listing_event(pub, "Stolen bikes", "cheap", listing_id="bk1"), nsec)
    nostr.publish(ev, [hub.url])
    c = TestClient(web.app)
    c.post("/api/moderation/hide", json={"id": ev.id, "kind": 30402, "pubkey": pub, "d": "bk1"})
    edit = sign_event(classifieds.build_listing_event(pub, "Stolen bikes v2", "cheaper", listing_id="bk1"), nsec)
    nostr.publish(edit, [hub.url])
    assert c.get("/api/classifieds").json()["listings"] == []


def test_blocked_key_disappears_and_cannot_post(hub):
    spam, keys = _post(hub, "scam one")
    c = TestClient(web.app)
    assert c.post("/api/moderation/block", json={"pubkey": spam.pubkey, "note": "scammer"}).status_code == 200
    _post(hub, "scam two, posted straight to the relay", keys)
    assert _board_texts(c) == []
    # Through the hub's publish endpoint: refused outright.
    nsec, _, pub = keys
    ev = sign_event(board.build_post(pub, "scam three", "news"), nsec)
    r = c.post("/api/publish", json={"event": ev.to_json()})
    assert r.status_code == 403 and "blocked" in r.json()["detail"]
    assert c.post("/api/moderation/unblock", json={"pubkey": spam.pubkey}).json() == {"unblocked": True}
    # Only the post from before the block: the relay refused the one sent while blocked.
    assert _board_texts(c) == ["scam one"]


def test_sync_never_passes_on_hidden_or_blocked(hub, tmp_path):
    hidden, _ = _post(hub, "hidden notice")
    blocked, _ = _post(hub, "blocked person's notice")
    kept, _ = _post(hub, "ordinary notice")
    moderation.hide(hidden.id)
    moderation.block(blocked.pubkey)
    # Gossip and USB export both read through the same filter.
    collected = asyncio.run(sync.collect(hub.url, 0))
    assert {e.id for e in collected} == {kept.id}
    path = tmp_path / "bundle.jsonl"
    sync.write_bundle([hidden, blocked, kept], path)  # a bundle made on some other hub
    good, bad = sync.read_bundle(path)
    assert [e.id for e in good] == [kept.id] and bad == 0  # not loaded here


def test_guests_cannot_moderate(hub, monkeypatch):
    post, _ = _post(hub, "fine")
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    c = TestClient(web.app)
    assert c.post("/api/moderation/hide", json={"id": post.id}).status_code == 403
    assert c.post("/api/moderation/block", json={"pubkey": post.pubkey}).status_code == 403
    assert c.get("/api/moderation").status_code == 403
    assert c.get("/moderation").status_code == 403
    assert 'href="/moderation"' not in c.get("/").text
    assert _board_texts(c) == ["fine"]


def test_bad_input_is_explained(hub):
    c = TestClient(web.app)
    assert c.post("/api/moderation/hide", json={"id": "nope"}).status_code == 400
    assert c.post("/api/moderation/block", json={"pubkey": "npub1bad"}).status_code == 400
    assert c.post("/api/moderation/frobnicate", json={}).status_code == 404


def test_block_accepts_npub_and_cli_lists_it(hub, capsys):
    from rag.identity import _hex_to_bech32
    _, _, pub = generate_keypair()
    assert moderation.main(["block", _hex_to_bech32("npub", pub), "spam"]) == 0
    assert moderation.is_blocked(pub)
    moderation.main(["list"])
    assert "Blocked: 1" in capsys.readouterr().out
