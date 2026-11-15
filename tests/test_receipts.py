from __future__ import annotations

import asyncio
import json

import pytest

from rag import receipts, reputation
from rag.classifieds import build_listing_event
from rag.identity import generate_keypair
from rag.nostr import publish, sign_event
from tests.mini_relay import MiniRelay


def _party():
    nsec, npub, pub = generate_keypair()
    return nsec, npub, pub


def _half(nsec, pub, body):
    return sign_event(receipts.build_receipt_event(pub, body), nsec)


def test_code_round_trip():
    _, _, a = _party()
    _, _, b = _party()
    body = receipts.make_body(a, b, "0.3 oz Ag", "20 lb apples", listing="apl-1")
    assert receipts.decode_code(receipts.encode_code(body)) == body


def test_damaged_code_rejected():
    with pytest.raises(ValueError):
        receipts.decode_code("not-a-real-code!!")
    with pytest.raises(ValueError):
        receipts.decode_code(receipts.encode_code({"id": "x"})[:10])


def test_make_body_validation():
    _, _, a = _party()
    with pytest.raises(ValueError):
        receipts.make_body(a, a, "x", "y")
    _, _, b = _party()
    with pytest.raises(ValueError):
        receipts.make_body(a, b, "  ", "y")


def test_accepts_npub_or_hex():
    _, npub, pub = _party()
    assert receipts._hex_pub(npub) == pub
    assert receipts._hex_pub(pub) == pub
    with pytest.raises(ValueError):
        receipts._hex_pub("bob")


def test_outsider_cannot_sign_half():
    _, _, a = _party()
    _, _, b = _party()
    _, _, c = _party()
    body = receipts.make_body(a, b, "x", "y")
    with pytest.raises(ValueError):
        receipts.build_receipt_event(c, body)


def test_only_two_matching_halves_confirm():
    na, _, a = _party()
    nb, _, b = _party()
    body = receipts.make_body(a, b, "eggs", "bread")
    ha = _half(na, a, body)
    assert receipts.confirmed_receipts([ha]) == []
    hb = _half(nb, b, body)
    assert [r["id"] for r in receipts.confirmed_receipts([ha, hb])] == [body["id"]]


def test_mismatched_bodies_do_not_confirm():
    na, _, a = _party()
    nb, _, b = _party()
    body = receipts.make_body(a, b, "eggs", "bread")
    altered = dict(body, got="two loaves of bread")
    assert receipts.confirmed_receipts([_half(na, a, body), _half(nb, b, altered)]) == []


def test_forged_body_hash_ignored():
    na, _, a = _party()
    nb, _, b = _party()
    body = receipts.make_body(a, b, "eggs", "bread")
    ev = receipts.build_receipt_event(a, body)
    ev.tags = [t if t[0] != "body" else ["body", "0" * 64] for t in ev.tags]
    sign_event(ev, na)
    assert receipts.confirmed_receipts([ev, _half(nb, b, body)]) == []


def test_sockpuppet_self_trade_cannot_confirm():
    """A third key can't stand in for the counterparty."""
    na, _, a = _party()
    _, _, b = _party()
    nc, _, c = _party()
    body = receipts.make_body(a, b, "x", "y")
    fake = receipts.build_receipt_event(a, body)
    fake.pubkey = c  # claims to be another half, signed by c
    sign_event(fake, nc)
    assert receipts.confirmed_receipts([_half(na, a, body), fake]) == []


def test_reputation_counts():
    na, _, a = _party()
    nb, _, b = _party()
    nc, _, c = _party()
    trades = []
    for other_n, other in ((nb, b), (nc, c), (nb, b)):
        body = receipts.make_body(a, other, "work", "pay")
        trades += [_half(na, a, body), _half(other_n, other, body)]
    pending = receipts.make_body(a, c, "unconfirmed", "x")
    trades.append(_half(na, a, pending))
    live = sign_event(build_listing_event(a, "Honey", ""), na)
    sold = sign_event(build_listing_event(a, "Eggs", "", status="sold"), na)
    lapsed = sign_event(build_listing_event(a, "Old", "", ttl_days=-1), na)
    r = reputation.compute_from_events(a, trades, [live, sold, lapsed])
    assert r["confirmed_trades"] == 3
    assert r["trade_partners"] == 2
    assert (r["listings_kept_up"], r["listings_lapsed"]) == (2, 1)
    line = reputation.summary_line(r)
    assert "3 confirmed trades" in line and "2/3 listings kept up" in line


def test_reputation_new_user():
    _, _, a = _party()
    r = reputation.compute_from_events(a, [], [])
    assert reputation.summary_line(r).startswith("new here")


def test_draft_confirm_and_reputation_over_relay(tmp_path, monkeypatch):
    relay = MiniRelay().start()
    try:
        monkeypatch.setattr(receipts, "_load_relays_from_config", lambda: [relay.url])
        monkeypatch.setattr(receipts, "MINE_PATH", tmp_path / "a.json")
        na, npub_a, a = _party()
        nb, npub_b, b = _party()

        class A:
            hex_pub = a

        class B:
            hex_pub = b

        body, code, res = receipts.draft(A, na, npub_b, "0.3 oz Ag", "20 lb apples")
        assert res[relay.url][0]
        r = asyncio.run(reputation.acompute([relay.url], npub_a))
        assert r["confirmed_trades"] == 0  # only Alice's half so far

        monkeypatch.setattr(receipts, "MINE_PATH", tmp_path / "b.json")
        body_b, _ = receipts.confirm(B, nb, code)
        assert body_b == body
        assert receipts.describe(body_b, b) == "I gave 20 lb apples and got 0.3 oz Ag"

        for who in (npub_a, npub_b):
            assert asyncio.run(reputation.acompute([relay.url], who))["confirmed_trades"] == 1
    finally:
        relay.stop()


def test_web_receipt_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from rag import identity, nostr
    from rag.web import app

    relay = MiniRelay().start()
    try:
        monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
        monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
        nostr._save_relays([relay.url])
        monkeypatch.setattr(receipts, "MINE_PATH", tmp_path / "mine.json")
        client = TestClient(app, raise_server_exceptions=False)

        na, _, _ = _party()
        identity.save_identity("alice", na)
        nb, npub_b, _ = _party()
        identity.save_identity("bob", nb)

        identity.set_active("alice")
        assert client.get("/receipts").status_code == 200
        r = client.post("/api/receipts", json={"with": npub_b, "gave": "eggs", "got": "bread"})
        assert r.status_code == 200, r.text
        code = r.json()["code"]
        assert r.json()["qr"].startswith("<svg")
        assert client.post("/api/receipts", json={"with": "bob", "gave": "x", "got": "y"}).status_code == 400

        identity.set_active("bob")
        r = client.post("/api/receipts/confirm", json={"code": code})
        assert r.status_code == 200 and r.json()["text"] == "I gave bread and got eggs"
        rows = client.get("/api/receipts").json()["receipts"]
        assert rows and rows[0]["confirmed"] is True

        rep = client.get("/api/reputation/" + npub_b).json()
        assert rep["confirmed_trades"] == 1
        assert client.get("/api/reputation/not-a-key").status_code == 400
    finally:
        relay.stop()
