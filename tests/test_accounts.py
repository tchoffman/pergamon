import asyncio
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag import accounts, web
from rag.identity import generate_keypair
from rag.nostr import Event, sign_event

NODE = shutil.which("node")


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = accounts.Accounts(tmp_path / "accounts.db")
    monkeypatch.setattr(accounts, "store", s)
    return s


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_sign_up_and_in_from_two_phones_with_the_real_browser_code(store):
    import uvicorn
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        out = subprocess.run([NODE, str(Path(__file__).parent / "js_account_flow.js"),
                              f"http://127.0.0.1:{port}"], capture_output=True, text=True, timeout=120)
    finally:
        server.should_exit = True
        t.join(5)
    r = json.loads(out.stdout or out.stderr)
    assert "crash" not in r, r
    assert r["signup_npub"].startswith("npub1") and r["signup_username"] == "rosa"
    assert r["taken"] == "That username is taken on this hub."
    assert r["wrong_password"] == "Wrong username or password."
    assert r["second_phone_same_npub"] is True
    assert r["unknown_user"] == "No account by that name on this hub."
    assert r["keyfile_prefix"] == "ncryptsec1" and r["keyfile_wrong"] == "wrong password"
    assert r["keyfile_same_npub"] is True
    assert "only on its owner's phone" in r["phone_only"]
    assert r["signed"] is True and r["signed_out"] is True
    # The hub never stored a password or an unlocked key.
    acct = store.get("rosa")
    assert acct.ncryptsec.startswith("ncryptsec1") and store.get("ben").ncryptsec is None


def _proof(username, nsec_pub=None, kind=27235, age=0):
    nsec, _, pub = nsec_pub or generate_keypair()
    ev = Event(kind=kind, content="pergamon account sign-up", pubkey=pub,
               tags=[["username", username]], created_at=int(time.time()) - age)
    sign_event(ev, nsec)
    return pub, {"kind": ev.kind, "content": ev.content, "pubkey": ev.pubkey, "tags": ev.tags,
                 "created_at": ev.created_at, "id": ev.id, "sig": ev.sig}


SIGNUP = {"salt": "ab" * 16, "log_n": 16, "auth": "cd" * 32, "ncryptsec": None}


def test_signup_needs_a_fresh_proof_for_that_username(store):
    c = TestClient(web.app)
    _, proof = _proof("other")
    assert "something else" in c.post("/api/account/signup", json={**SIGNUP, "username": "rosa", "proof": proof}).text
    _, proof = _proof("rosa", age=3600)
    assert "stale" in c.post("/api/account/signup", json={**SIGNUP, "username": "rosa", "proof": proof}).text
    _, proof = _proof("rosa")
    proof["content"] = "tampered"
    assert "doesn't verify" in c.post("/api/account/signup", json={**SIGNUP, "username": "rosa", "proof": proof}).text
    _, proof = _proof("rosa")
    r = c.post("/api/account/signup", json={**SIGNUP, "username": "Rosa ", "proof": proof})
    assert r.status_code == 200 and r.json()["username"] == "rosa"  # tidied, not refused


def test_signup_rejects_weak_locks_and_bad_names(store):
    c = TestClient(web.app)
    for bad in ({"log_n": 8}, {"salt": "zz"}, {"auth": "short"}, {"ncryptsec": "nsec1abc"}):
        _, proof = _proof("rosa")
        assert c.post("/api/account/signup", json={**SIGNUP, **bad, "username": "rosa", "proof": proof}).status_code == 400
    _, proof = _proof("a")
    assert "Usernames are" in c.post("/api/account/signup", json={**SIGNUP, "username": "a", "proof": proof}).text


def test_wrong_guesses_are_limited_per_account(store):
    pub, proof = _proof("rosa")
    c = TestClient(web.app)
    assert c.post("/api/account/signup", json={**SIGNUP, "username": "rosa", "proof": proof}).status_code == 200
    for _ in range(accounts.FAILS_PER_WINDOW):
        assert c.post("/api/account/signin", json={"username": "rosa", "auth": "ee" * 32}).status_code == 401
    r = c.post("/api/account/signin", json={"username": "rosa", "auth": "cd" * 32})
    assert r.status_code == 429  # even the right password waits now
    later = time.time() + accounts.FAIL_WINDOW_S + 1
    assert store.sign_in("rosa", "cd" * 32, "other-phone", now=later).pubkey == pub


def test_salt_lookup(store):
    _, proof = _proof("rosa")
    c = TestClient(web.app)
    c.post("/api/account/signup", json={**SIGNUP, "username": "rosa", "proof": proof})
    assert c.get("/api/account/salt", params={"username": "ROSA"}).json() == {"salt": "ab" * 16, "log_n": 16}
    assert c.get("/api/account/salt", params={"username": "nobody"}).status_code == 404
