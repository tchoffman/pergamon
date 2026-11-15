"""Browser-held keys: server drafts, pergamon.js signs (run under Node),
/api/publish verifies and relays."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag import classifieds, identity, nostr, receipts, skills
from rag.identity import generate_keypair
from rag.web import app
from tests.mini_relay import MiniRelay

NODE = shutil.which("node")
SIGNER = Path(__file__).parent / "js_sign_check.js"
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


def js_sign(unsigned: dict, nsec: str) -> dict:
    out = subprocess.run([NODE, str(SIGNER), json.dumps(unsigned), "import", nsec],
                         capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)["event"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    relay = MiniRelay().start()
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")  # no active identity at all
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([relay.url])
    monkeypatch.setattr(classifieds, "MINE_PATH", tmp_path / "cl.json")
    monkeypatch.setattr(skills, "MINE_PATH", tmp_path / "sk.json")
    monkeypatch.setattr(receipts, "MINE_PATH", tmp_path / "rc.json")
    yield TestClient(app, raise_server_exceptions=False), relay
    relay.stop()


def _publish(client, draft_url, body, nsec):
    r = client.post(draft_url, json=body)
    assert r.status_code == 200, r.text
    signed = js_sign(r.json()["event"], nsec)
    return client.post("/api/publish", json={"event": signed}), signed, r.json()


def test_board_post_signed_in_browser(env):
    client, relay = env
    nsec, _, pub = generate_keypair()
    r, signed, _ = _publish(client, "/api/draft/board",
                            {"pubkey": pub, "category": "news", "text": "Ferry running ☀"}, nsec)
    assert r.status_code == 200, r.text
    assert r.json()["published_to"][relay.url]["ok"] is True
    assert relay.events[signed["id"]]["content"] == "Ferry running ☀"


def test_classified_post_renew_close_from_browser(env):
    client, relay = env
    nsec, _, pub = generate_keypair()
    r, signed, _ = _publish(client, "/api/draft/classifieds",
                            {"pubkey": pub, "title": "Goat milk", "side": "offering"}, nsec)
    assert r.status_code == 200
    d = next(t[1] for t in signed["tags"] if t[0] == "d")
    mine = client.get(f"/api/classifieds/mine?pubkey={pub}").json()
    assert [l["d"] for l in mine["listings"]] == [d]

    r, closed, _ = _publish(client, f"/api/draft/classifieds/{d}/close", {"pubkey": pub}, nsec)
    assert r.status_code == 200
    assert ["status", "sold"] in closed["tags"]
    # Someone else's key can't get a draft for this listing.
    other = generate_keypair()[2]
    assert client.post(f"/api/draft/classifieds/{d}/renew",
                       json={"pubkey": other}).status_code == 404


def test_receipt_between_two_browsers(env):
    client, _ = env
    na, npub_a, a = generate_keypair()
    nb, npub_b, b = generate_keypair()
    r, _, draft = _publish(client, "/api/draft/receipt",
                           {"pubkey": a, "with": npub_b, "gave": "eggs", "got": "bread"}, na)
    assert r.status_code == 200
    code = draft["code"]
    r, _, conf = _publish(client, "/api/draft/receipt/confirm", {"pubkey": b, "code": code}, nb)
    assert r.status_code == 200 and conf["text"] == "I gave bread and got eggs"
    rows = client.get(f"/api/receipts?pubkey={b}").json()["receipts"]
    assert rows[0]["confirmed"] is True
    assert client.get(f"/api/reputation/{npub_a}").json()["confirmed_trades"] == 1


def test_skill_post_and_close_from_browser(env):
    client, relay = env
    nsec, _, pub = generate_keypair()
    r, signed, _ = _publish(client, "/api/draft/skills",
                            {"pubkey": pub, "title": "Welding", "category": "metalwork",
                             "accepts": "barter, silver", "radius_mi": "10"}, nsec)
    assert r.status_code == 200, r.text
    d = next(t[1] for t in signed["tags"] if t[0] == "d")
    mine = client.get(f"/api/skills/mine?pubkey={pub}").json()["skills"]
    assert [s["title"] for s in mine] == ["Welding"]
    assert mine[0]["accepts"] == ["barter", "silver"]
    assert [s["title"] for s in client.get("/api/skills?q=weld").json()["skills"]] == ["Welding"]
    r, closed, _ = _publish(client, f"/api/draft/skills/{d}/close", {"pubkey": pub}, nsec)
    assert r.status_code == 200 and ["status", "closed"] in closed["tags"]
    assert client.get("/api/skills?q=weld").json()["skills"] == []
    assert client.post("/api/draft/skills", json={"pubkey": pub, "title": "x",
                                                  "category": "wizardry"}).status_code == 400


def test_profile_from_browser(env):
    client, _ = env
    nsec, _, pub = generate_keypair()
    r, signed, _ = _publish(client, "/api/draft/profile",
                            {"pubkey": pub, "display_name": "Rosa", "about": "beekeeper"}, nsec)
    assert r.status_code == 200
    assert json.loads(signed["content"]) == {"display_name": "Rosa", "about": "beekeeper"}


def test_publish_rejects_bad_events(env):
    client, _ = env
    nsec, _, pub = generate_keypair()
    draft = client.post("/api/draft/board",
                        json={"pubkey": pub, "category": "news", "text": "real"}).json()["event"]
    signed = js_sign(draft, nsec)

    tampered = dict(signed, content="forged")
    assert client.post("/api/publish", json={"event": tampered}).status_code == 400

    stray = js_sign({**draft, "tags": []}, nsec)  # a kind-1 note without the board tag
    assert client.post("/api/publish", json={"event": stray}).status_code == 400

    dm = js_sign({**draft, "kind": 4}, nsec)
    assert client.post("/api/publish", json={"event": dm}).status_code == 400

    old = js_sign({**draft, "created_at": draft["created_at"] - 10 * 86400}, nsec)
    assert client.post("/api/publish", json={"event": old}).status_code == 400

    assert client.post("/api/publish", json={"event": {"kind": 1}}).status_code == 400
    assert client.post("/api/draft/board", json={"pubkey": "zz", "text": "x"}).status_code == 400


def test_pages_load_signing_scripts(env):
    client, _ = env
    for page in ("/board", "/classifieds", "/receipts", "/identity", "/welcome", "/skills"):
        html = client.get(page).text
        assert "/static/vendor/noble-nostr.js" in html, page
        assert "/static/pergamon.js" in html, page
