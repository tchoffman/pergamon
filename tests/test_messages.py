"""End-to-end encrypted private messages (NIP-17 gift wraps)."""
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag import messages, nip44, nostr, sync, web
from rag.identity import _bech32_to_hex, generate_keypair
from rag.nostr import Event, sign_event
from tests.mini_relay import MiniRelay

NODE = shutil.which("node")
JS = Path(__file__).parent / "js_messages_check.js"


def keys():
    nsec, _, pub = generate_keypair()
    return nsec, pub


def test_only_the_recipient_and_sender_can_read():
    (a_nsec, a), (b_nsec, b), (c_nsec, _) = keys(), keys(), keys()
    to_b, copy = messages.build(a_nsec, b, "Meet at the mill at noon")
    assert messages.open_wrap(b_nsec, to_b)["text"] == "Meet at the mill at noon"
    assert messages.open_wrap(a_nsec, copy)["from"] == a
    with pytest.raises(ValueError):
        messages.open_wrap(c_nsec, to_b)


def test_wrap_reveals_only_the_recipient():
    (a_nsec, a), (_, b) = keys(), keys()
    wrap, _ = messages.build(a_nsec, b, "secret plans")
    assert wrap.tags == [["p", b]] and wrap.pubkey != a
    assert a not in json.dumps(wrap.to_json()) and "secret" not in wrap.content
    assert time.time() - 2 * 86400 - 5 <= wrap.created_at <= time.time() + 5


def test_forged_sender_is_rejected():
    (m_nsec, m), (b_nsec, b), (_, alice) = keys(), keys(), keys()
    msec = _bech32_to_hex("nsec", m_nsec)
    rumor = {"pubkey": alice, "created_at": int(time.time()), "kind": 14, "tags": [["p", b]],
             "content": "Alice here, send the money"}
    rumor["id"] = messages._rumor_id(rumor)
    # A properly signed seal from Mallory, around a message claiming to be Alice's.
    seal = Event(kind=13, pubkey=m, tags=[], created_at=int(time.time()),
                 content=nip44.encrypt(json.dumps(rumor), nip44.conversation_key(msec, b)))
    sign_event(seal, m_nsec)
    e_nsec, _, e_pub = generate_keypair()
    wrap = Event(kind=1059, pubkey=e_pub, tags=[["p", b]], created_at=int(time.time()),
                 content=nip44.encrypt(json.dumps(seal.to_json()),
                                       nip44.conversation_key(_bech32_to_hex("nsec", e_nsec), b)))
    sign_event(wrap, e_nsec)
    with pytest.raises(ValueError, match="forged"):
        messages.open_wrap(b_nsec, wrap)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_browser_and_python_read_each_other():
    (a_nsec, a), (b_nsec, b) = keys(), keys()
    out = subprocess.run([NODE, str(JS), "build", a_nsec, b, "from the browser ☀"],
                         capture_output=True, text=True, check=True).stdout
    wrap = messages._event(json.loads(out)[0])
    assert messages.open_wrap(b_nsec, wrap)["text"] == "from the browser ☀"
    py_wrap, _ = messages.build(b_nsec, a, "from python")
    opened = json.loads(subprocess.run([NODE, str(JS), "open", a_nsec, json.dumps(py_wrap.to_json())],
                                       capture_output=True, text=True, check=True).stdout)
    assert opened["text"] == "from python" and opened["from"] == b


def _no_wrap_links(events, a, b):
    """Nothing visible ties sender a to recipient b: no wrap is signed by a,
    and none names both. (a's own sent-copy is a separate wrap addressed to a,
    with a different throwaway signer and a blurred time.)"""
    wraps = [e for e in events if e["kind"] == 1059]
    assert wraps
    for w in wraps:
        assert w["pubkey"] not in (a, b)
        named = {t[1] for t in w["tags"] if t[0] == "p"}
        assert len(named) == 1 and not {a, b} <= named
    assert len({w["pubkey"] for w in wraps}) == len(wraps)  # a fresh signer every time


@pytest.fixture
def hub(tmp_path, monkeypatch):
    relay = MiniRelay().start()
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([relay.url])
    from rag import moderation
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    yield relay
    relay.stop()


def _proof(nsec, pub, purpose="inbox", age=0):
    ev = Event(kind=27235, pubkey=pub, tags=[["purpose", purpose]], content="pergamon inbox",
               created_at=int(time.time()) - age)
    return sign_event(ev, nsec).to_json()


def test_hub_relays_wraps_it_cannot_read(hub):
    (a_nsec, a), (b_nsec, b) = keys(), keys()
    c = TestClient(web.app)
    for w in messages.build(a_nsec, b, "the spare key is under the pot"):
        assert c.post("/api/publish", json={"event": w.to_json()}).status_code == 200
    stored = list(hub.events.values())
    assert "spare key" not in json.dumps(stored)  # the host can't read it
    _no_wrap_links(stored, a, b)
    inbox = c.post("/api/messages/inbox", json={"proof": _proof(b_nsec, b)}).json()["wraps"]
    assert [messages.open_wrap(b_nsec, messages._event(w))["text"] for w in inbox] == \
        ["the spare key is under the pot"]
    mine = c.post("/api/messages/inbox", json={"proof": _proof(a_nsec, a)}).json()["wraps"]
    assert len(mine) == 1  # the sender's own copy


def test_inbox_needs_a_fresh_proof_from_the_owner(hub):
    (a_nsec, a), (b_nsec, b) = keys(), keys()
    c = TestClient(web.app)
    assert c.post("/api/messages/inbox", json={}).status_code == 400
    assert c.post("/api/messages/inbox", json={"proof": _proof(a_nsec, a, age=3600)}).status_code == 401
    assert c.post("/api/messages/inbox", json={"proof": _proof(a_nsec, a, purpose="other")}).status_code == 401


def test_malformed_wraps_are_refused(hub):
    (a_nsec, a), (_, b), (_, c2) = keys(), keys(), keys()
    wrap, _ = messages.build(a_nsec, b, "hi")
    eph = Event(kind=1059, pubkey="", tags=[["p", b], ["p", c2]], content=wrap.content)
    nsec, _, pub = generate_keypair()
    eph.pubkey = pub
    sign_event(eph, nsec)
    r = TestClient(web.app).post("/api/publish", json={"event": eph.to_json()})
    assert r.status_code == 400 and "exactly one recipient" in r.json()["detail"]


def test_message_reaches_another_hub_by_usb_without_naming_the_sender(hub, tmp_path):
    (a_nsec, a), (b_nsec, b) = keys(), keys()
    for w in messages.build(a_nsec, b, "Letter for the next town"):
        nostr.publish(w, [hub.url])
    # Hub 1 exports a courier bundle.
    events = asyncio.run(sync.collect(hub.url, 0))
    path = tmp_path / "stick.jsonl"
    sync.write_bundle(events, path)
    stick = [json.loads(line) for line in path.read_text().splitlines()]
    assert "Letter" not in path.read_text()
    _no_wrap_links(stick, a, b)  # who wrote to whom isn't on the stick
    # Hub 2 imports it; Bob reads his mail there.
    other = MiniRelay().start()
    try:
        good, bad = sync.read_bundle(path)
        assert bad == 0
        asyncio.run(sync.publish_batch(other.url, good))
        wraps = asyncio.run(nostr.afetch_events([other.url], {"kinds": [1059], "#p": [b]}, 50))
        assert [messages.open_wrap(b_nsec, w)["text"] for w in wraps] == ["Letter for the next town"]
    finally:
        other.stop()
