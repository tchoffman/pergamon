from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from rag import ledger, library
from rag.identity import generate_keypair
from rag.nostr import publish, sign_event
from tests.mini_relay import MiniRelay


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "DB_DIR", tmp_path / "db")


def _add(**kv):
    return ledger.add_row("library", {k: str(v) for k, v in kv.items()})


def test_update_row_and_get_row():
    rid = _add(title="Dune", type="book")
    assert ledger.update_row("library", rid, {"condition": "worn"})
    assert ledger.get_row("library", rid)["condition"] == "worn"
    assert ledger.update_row("library", 999, {"condition": "worn"}) is False
    with pytest.raises(ValueError):
        ledger.update_row("library", rid, {"nope": "x"})


def test_lend_and_return_cycle():
    rid = _add(title="Chainsaw", type="tool", lendable="yes")
    lid = library.lend(rid, "npub1neighbor", due="7d")
    assert library.open_loan_for(rid)["id"] == lid
    with pytest.raises(ValueError):
        library.lend(rid, "npub1someoneelse")
    assert library.return_loan(lid, "chain dull")
    assert library.open_loan_for(rid) is None
    assert ledger.get_row("loans", lid)["condition_change"] == "chain dull"


def test_borrowed_direction_does_not_block_lending():
    rid = _add(title="Ladder", type="tool")
    library.lend(rid, "npub1owner", direction="borrowed")
    assert library.open_loan_for(rid) is None


def test_overdue():
    rid = _add(title="Book", type="book")
    lid = library.lend(rid, "bob", due=(date.today() - timedelta(days=1)).isoformat())
    assert [r["id"] for r in library.overdue()] == [lid]
    library.return_loan(lid)
    assert library.overdue() == []


def test_lend_unknown_item():
    with pytest.raises(KeyError):
        library.lend(42, "bob")


def test_catalog_items_public_fields_only():
    a = _add(title="Where There Is No Doctor", type="book", creator="Hesperian",
             lendable="yes", location="shelf A3", serial="X1", notes="private")
    _add(title="Grandma's ring", type="other")  # not lendable → not published
    _add(title="Seed: Cherokee Purple", type="seed", seedable="yes")
    lid = library.lend(a, "bob")
    items = library.catalog_items()
    titles = {i["title"] for i in items}
    assert titles == {"Where There Is No Doctor", "Seed: Cherokee Purple"}
    doc = next(i for i in items if i["title"].startswith("Where"))
    assert doc["available"] is False
    assert set(doc) <= {"title", "type", "creator", "available", "seedable"}
    library.return_loan(lid)
    assert all(i["available"] for i in library.catalog_items())


def test_catalog_event_and_search():
    nsec, _, pub = generate_keypair()
    items = [{"title": "Stihl chainsaw", "type": "tool", "available": True},
             {"title": "Dune", "type": "book", "creator": "Frank Herbert", "available": True}]
    ev = sign_event(library.build_catalog_event(pub, items), nsec)
    assert ev.kind == library.KIND_CATALOG
    assert ["d", library.CATALOG_D] in ev.tags
    assert [h["title"] for h in library.search_catalogs([ev], "chainsaw")] == ["Stihl chainsaw"]
    assert [h["title"] for h in library.search_catalogs([ev], "herbert")] == ["Dune"]
    assert [h["title"] for h in library.search_catalogs([ev], "", kind="tool")] == ["Stihl chainsaw"]


def test_expired_catalog_ignored():
    nsec, _, pub = generate_keypair()
    ev = library.build_catalog_event(pub, [{"title": "Old", "type": "book"}])
    ev.tags = [t for t in ev.tags if t[0] != "expires_at"] + [["expires_at", "1"]]
    sign_event(ev, nsec)
    assert library.search_catalogs([ev]) == []


def test_newest_catalog_wins():
    nsec, _, pub = generate_keypair()
    old = library.build_catalog_event(pub, [{"title": "Sold axe", "type": "tool"}])
    old.created_at -= 100
    sign_event(old, nsec)
    new = sign_event(library.build_catalog_event(pub, [{"title": "New saw", "type": "tool"}]), nsec)
    assert [h["title"] for h in library.search_catalogs([old, new])] == ["New saw"]


def test_find_over_relay():
    relay = MiniRelay().start()
    try:
        nsec, _, pub = generate_keypair()
        ev = sign_event(library.build_catalog_event(
            pub, [{"title": "Pressure canner", "type": "tool", "available": True}]), nsec)
        publish(ev, [relay.url])
        hits = asyncio.run(library.afind([relay.url], "canner"))
        assert [h["title"] for h in hits] == ["Pressure canner"]
        assert hits[0]["owner"] == pub
    finally:
        relay.stop()
