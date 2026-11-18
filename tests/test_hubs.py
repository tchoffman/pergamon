"""Trusted hubs: gossip only with verified trusted hubs; sticks from anyone
else are reviewed and accepted once."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from rag import board, hubs, moderation, nostr, sync, web
from rag.identity import generate_keypair
from rag.nostr import sign_event


def _other_hub(tmp_path, monkeypatch, name="Smyrna"):
    """Pretend to be a second hub: its key, its code."""
    nsec, npub, pub = generate_keypair()
    code = f"pergamon-hub:?k={npub}&n={name}&w=http://smyrna.local:8090&r=ws://smyrna.local:8091/"
    return nsec, pub, code


def _hello_from(nsec, pub, challenge, age=0):
    from rag.nostr import Event
    ev = Event(kind=hubs.KIND_HELLO, pubkey=pub, tags=[["challenge", challenge], ["purpose", "hub-hello"]],
               content="x", created_at=int(time.time()) - age)
    return {"event": sign_event(ev, nsec).to_json()}


def test_code_round_trip_and_trust(tmp_path, monkeypatch):
    code = hubs.code()
    info = hubs.parse_code(code)
    assert info["pubkey"] == hubs.pubkey()
    with pytest.raises(hubs.TrustError, match="own code"):
        hubs.trust(info)
    _, pub, other = _other_hub(tmp_path, monkeypatch)
    hubs.trust(hubs.parse_code(other))
    assert hubs.find("Smyrna")[0] == pub and hubs.find("ws://smyrna.local:8091")[0] == pub
    assert hubs.untrust("Smyrna") and not hubs.trusted()
    with pytest.raises(hubs.TrustError, match="isn't a hub code"):
        hubs.parse_code("hello")


def test_gossip_refused_unless_trusted_and_proven(tmp_path, monkeypatch):
    nsec, pub, code = _other_hub(tmp_path, monkeypatch)
    with pytest.raises(hubs.TrustError, match="isn't a trusted hub"):
        hubs.verify_remote("ws://smyrna.local:8091/")
    hubs.trust(hubs.parse_code(code))
    seen = {}

    def honest(url):
        seen["challenge"] = url.split("challenge=")[1]
        return _hello_from(nsec, pub, seen["challenge"])
    assert hubs.verify_remote("Smyrna", fetch=honest)[0] == pub
    # An impostor at the same address, with a different key:
    imp_nsec, _, imp_pub = generate_keypair()
    with pytest.raises(hubs.TrustError, match="couldn't prove"):
        hubs.verify_remote("Smyrna", fetch=lambda url: _hello_from(imp_nsec, imp_pub, url.split("=")[1]))
    # A replayed old answer:
    with pytest.raises(hubs.TrustError, match="couldn't prove"):
        hubs.verify_remote("Smyrna", fetch=lambda url: _hello_from(nsec, pub, "ab" * 32))
    with pytest.raises(hubs.TrustError, match="Couldn't reach"):
        hubs.verify_remote("Smyrna", fetch=lambda url: (_ for _ in ()).throw(OSError("no route")))


def test_sync_command_refuses_an_untrusted_hub(capsys):
    assert sync.main(["pull", "ws://stranger.local:8091/"]) == 1
    assert "isn't a trusted hub" in capsys.readouterr().out


def test_hello_endpoint_signs_the_challenge():
    c = TestClient(web.app)
    r = c.get("/api/hub/hello", params={"challenge": "cd" * 32}).json()
    assert r["event"]["pubkey"] == hubs.pubkey() and ["challenge", "cd" * 32] in r["event"]["tags"]
    assert c.get("/api/hub/hello", params={"challenge": "x"}).status_code == 400


def _posts(n, keys=None):
    nsec, _, pub = keys or generate_keypair()
    return [sign_event(board.build_post(pub, f"notice {i}", "news"), nsec) for i in range(n)]


def test_bundle_manifest_says_who_and_detects_tampering(tmp_path):
    path = tmp_path / "stick.jsonl"
    sync.write_bundle(_posts(3), path)
    src = sync.bundle_source(path)
    assert src["signed"] and src["intact"] and src["pubkey"] == hubs.pubkey() and not src["trusted"]
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")  # a courier dropped an item
    assert not sync.bundle_source(path)["intact"]
    unsigned = tmp_path / "plain.jsonl"
    sync.write_bundle(_posts(1), unsigned, signed=False)
    assert not sync.bundle_source(unsigned)["signed"]
    assert len(sync.read_bundle(unsigned)[0]) == 1  # old-style bundles still read


@pytest.fixture
def relay(tmp_path, monkeypatch):
    from tests.mini_relay import MiniRelay
    r = MiniRelay().start()
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([r.url])
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    yield r
    r.stop()


def _foreign_bundle(tmp_path, monkeypatch, posts):
    """A bundle made by another hub (its own key)."""
    path = tmp_path / "from-smyrna.jsonl"
    with monkeypatch.context() as m:
        m.setattr(hubs, "KEY_PATH", tmp_path / "smyrna.key")
        sync.write_bundle(posts, path)
        other_pub = hubs.pubkey()
    return path, other_pub


def test_stick_from_unknown_hub_is_reviewed_then_accepted_once(relay, tmp_path, monkeypatch, capsys):
    keys = generate_keypair()
    spammer = generate_keypair()
    posts = _posts(2, keys) + _posts(1, spammer)
    path, other_pub = _foreign_bundle(tmp_path, monkeypatch, posts)
    moderation.block(spammer[2])
    assert sync.main(["import", str(path)]) == 1
    out = capsys.readouterr().out
    assert "not a trusted hub" in out and "2 board posts" in out and "Not imported" in out
    assert relay.events == {}  # nothing loaded before the host decides
    assert sync.main(["import", str(path), "--accept"]) == 0
    assert len(relay.events) == 2  # the blocked key's post never comes in
    assert other_pub not in hubs.trusted()


def test_accept_and_trust_then_later_sticks_import_directly(relay, tmp_path, monkeypatch, capsys):
    path, other_pub = _foreign_bundle(tmp_path, monkeypatch, _posts(1))
    assert sync.main(["import", str(path), "--accept", "--trust"]) == 0
    assert other_pub in hubs.trusted()
    path2, _ = _foreign_bundle(tmp_path, monkeypatch, _posts(2))
    capsys.readouterr()
    assert sync.main(["import", str(path2)]) == 0
    assert "(trusted)" in capsys.readouterr().out and len(relay.events) == 3


def test_tampered_stick_from_trusted_hub_needs_review(relay, tmp_path, monkeypatch, capsys):
    path, other_pub = _foreign_bundle(tmp_path, monkeypatch, _posts(2))
    sync.main(["import", str(path), "--accept", "--trust"])
    path2, _ = _foreign_bundle(tmp_path, monkeypatch, _posts(2))
    extra = _posts(1)[0]
    with path2.open("a") as f:
        f.write(json.dumps(extra.to_json()) + "\n")  # slipped in after signing
    capsys.readouterr()
    assert sync.main(["import", str(path2)]) == 1
    assert "contents changed after signing" in capsys.readouterr().out


def test_hubs_page_is_host_only_and_trusts_by_code(tmp_path, monkeypatch):
    c = TestClient(web.app)
    page = c.get("/hubs").text
    assert "pergamon-hub:?" in page and "<svg" in page
    _, pub, code = _other_hub(tmp_path, monkeypatch)
    assert c.post("/api/hubs/trust", json={"code": code}).status_code == 200
    assert pub in hubs.trusted() and "Smyrna" in c.get("/hubs").text
    assert c.post("/api/hubs/trust", json={"code": "nope"}).status_code == 400
    assert c.post("/api/hubs/untrust", json={"who": pub}).json() == {"removed": True}
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    assert c.get("/hubs").status_code == 403
    assert c.post("/api/hubs/trust", json={"code": code}).status_code == 403
