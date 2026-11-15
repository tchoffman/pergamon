from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag import cards, identity, nostr
from rag.web import app


def test_qr_svg_is_inline_svg():
    svg = cards.qr_svg("npub1test")
    assert svg.startswith("<svg") and "path" in svg


def test_lan_urls_shape():
    for u in cards.lan_urls(8090):
        assert u.startswith("http://") and u.endswith(":8090/")


def test_card_html_contains_npub_and_four_copies():
    nsec, npub, hex_pub = identity.generate_keypair()
    ident = identity.Identity(name="alice", npub=npub, hex_pub=hex_pub,
                              display_name="Alice <script>", created="x", nip05=None)
    html = cards.card_html(ident)
    assert html.count('class="card"') == 4
    assert npub in html
    assert "<script>" not in html  # display name is escaped


def test_cli_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
    nsec, _, _ = identity.generate_keypair()
    identity.save_identity("bob", nsec)
    identity.set_active("bob")
    out = tmp_path / "card.html"
    assert cards.main(["--path", str(out)]) == 0
    assert out.read_text().count('class="card"') == 4


def test_web_card_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ID_DIR", tmp_path / "id")
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/identity/card").status_code == 400
    nsec, npub, _ = identity.generate_keypair()
    identity.save_identity("carol", nsec)
    identity.set_active("carol")
    r = client.get("/identity/card")
    assert r.status_code == 200 and npub in r.text
