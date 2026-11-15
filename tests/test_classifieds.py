"""Unit tests for rag.classifieds (NIP-99 kind 30402)."""
from __future__ import annotations

import pytest

from rag.classifieds import (
    KIND_CLASSIFIED,
    build_listing_event,
    summarize,
)
from rag.identity import generate_keypair
from rag.nostr import sign_event, verify_event


def _pub():
    _, _, hex_pub = generate_keypair()
    return hex_pub


# ──────────────────────── build + summarize ─────────────────────────

def test_build_sets_kind_and_d_tag():
    ev = build_listing_event(_pub(), "10 lb apples", "Honeycrisp, pick-up")
    assert ev.kind == KIND_CLASSIFIED
    d_tags = [t for t in ev.tags if t[0] == "d"]
    assert len(d_tags) == 1 and d_tags[0][1]   # non-empty id


def test_build_includes_title_and_description():
    ev = build_listing_event(_pub(), "apples", "Honeycrisp")
    tag_map = {t[0]: t[1:] for t in ev.tags}
    assert tag_map["title"] == ["apples"]
    assert ev.content == "Honeycrisp"


def test_build_price_in_silver():
    ev = build_listing_event(_pub(), "x", "y", price_oz_ag=0.3)
    prices = [t for t in ev.tags if t[0] == "price"]
    assert ["price", "0.3", "oz_ag"] in prices


def test_build_price_in_usd_and_silver_both():
    ev = build_listing_event(_pub(), "x", "y", price_oz_ag=0.3, price_usd=12.50)
    prices = [t for t in ev.tags if t[0] == "price"]
    assert ["price", "0.3", "oz_ag"] in prices
    assert ["price", "12.5", "USD"] in prices


def test_build_accepts_tag():
    ev = build_listing_event(_pub(), "x", "y", accepts=["silver", "barter", "labor"])
    accepts_tag = [t for t in ev.tags if t[0] == "accepts"]
    assert accepts_tag == [["accepts", "silver,barter,labor"]]


def test_build_hashtags_each_get_own_tag():
    ev = build_listing_event(_pub(), "x", "y", hashtags=["apples", "harvest"])
    ts = [t[1] for t in ev.tags if t[0] == "t"]
    assert ts == ["apples", "harvest"]


def test_build_includes_published_at_and_status():
    ev = build_listing_event(_pub(), "x", "y")
    kinds = {t[0] for t in ev.tags}
    assert "published_at" in kinds
    assert ["status", "active"] in ev.tags


def test_summarize_extracts_all_fields():
    ev = build_listing_event(
        _pub(), "20 lb apples", "Honeycrisp, pick-up only",
        price_oz_ag=0.3, price_usd=12.50,
        accepts=["silver", "barter"],
        location="Asheville NC",
        hashtags=["apples", "produce"],
        summary="Bulk apples",
    )
    info = summarize(ev)
    assert info["title"] == "20 lb apples"
    assert info["description"] == "Honeycrisp, pick-up only"
    assert info["price_oz_ag"] == 0.3
    assert info["price_usd"] == 12.50
    assert info["accepts"] == ["silver", "barter"]
    assert info["location"] == "Asheville NC"
    assert info["hashtags"] == ["apples", "produce"]
    assert info["status"] == "active"
    assert info["summary"] == "Bulk apples"


def test_summarize_handles_bare_event():
    ev = build_listing_event(_pub(), "minimal", "no extras")
    info = summarize(ev)
    assert info["title"] == "minimal"
    assert info["price_oz_ag"] is None
    assert info["accepts"] == []
    assert info["location"] == ""
    assert info["hashtags"] == []


def test_build_listing_round_trips_sign_and_verify():
    nsec, _, hex_pub = generate_keypair()
    ev = build_listing_event(
        hex_pub, "signed listing", "full desc",
        price_oz_ag=1.0, accepts=["silver"], hashtags=["test"],
    )
    sign_event(ev, nsec)
    assert verify_event(ev)


# ─────────────────────── freshness / replaceable ─────────────────────

import time as _time

from rag import classifieds as _c
from rag.classifieds import is_live, latest_per_listing, search_infos


def test_build_sets_side_and_expiry():
    ev = build_listing_event(_pub(), "x", "y", side="looking-for", ttl_days=7)
    info = summarize(ev)
    assert info["side"] == "looking-for"
    assert 6.9 * 86400 < info["expires_at"] - _time.time() < 7.1 * 86400
    assert info["is_live"] is True


def test_bad_side_rejected():
    with pytest.raises(ValueError):
        build_listing_event(_pub(), "x", "y", side="maybe")


def test_expired_listing_not_live():
    ev = build_listing_event(_pub(), "x", "y", ttl_days=-1)
    assert summarize(ev)["is_live"] is False


def test_sold_listing_not_live():
    ev = build_listing_event(_pub(), "x", "y", status="sold")
    assert summarize(ev)["is_live"] is False


def test_legacy_listing_without_expiry_uses_default_ttl():
    info = {"status": "active", "expires_at": None,
            "created_at": _time.time() - 30 * 86400}
    assert is_live(info) is False


def test_latest_per_listing_keeps_newest_version():
    pub = _pub()
    old = build_listing_event(pub, "apples", "v1", listing_id="abc")
    old.created_at = 1000
    new = build_listing_event(pub, "apples", "v2", listing_id="abc")
    new.created_at = 2000
    other = build_listing_event(pub, "pears", "p", listing_id="zzz")
    kept = latest_per_listing([old, new, other])
    contents = sorted(e.content for e in kept)
    assert contents == ["p", "v2"]


def test_search_infos_filters_side_and_text():
    pub = _pub()
    evs = [
        build_listing_event(pub, "Honey", "raw", side="offering"),
        build_listing_event(pub, "Honey wanted", "any", side="looking-for"),
        build_listing_event(pub, "Firewood", "oak"),
    ]
    assert [i["title"] for i in search_infos(evs, "honey", side="looking-for")] == ["Honey wanted"]
    assert len(search_infos(evs, "honey")) == 2


def test_republish_renews_same_d(tmp_path, monkeypatch):
    monkeypatch.setattr(_c, "MINE_PATH", tmp_path / "mine.json")
    nsec, _, pub = generate_keypair()
    ev = build_listing_event(pub, "x", "y", listing_id="keep-me", ttl_days=-1)
    sign_event(ev, nsec)
    _c._remember(ev)
    fresh = _c.republish("keep-me", nsec)
    info = summarize(fresh)
    assert info["d"] == "keep-me"
    assert info["is_live"] is True
    assert verify_event(fresh)


def test_republish_close_marks_sold(tmp_path, monkeypatch):
    monkeypatch.setattr(_c, "MINE_PATH", tmp_path / "mine.json")
    nsec, _, pub = generate_keypair()
    ev = build_listing_event(pub, "x", "y", listing_id="sell-me")
    sign_event(ev, nsec)
    _c._remember(ev)
    closed = _c.republish("sell-me", nsec, status="sold")
    assert summarize(closed)["status"] == "sold"


def test_soft_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(_c, "MINE_PATH", tmp_path / "mine.json")
    monkeypatch.setattr(_c, "publish", lambda ev, relays: {})
    nsec, _, pub = generate_keypair()

    class _Ident:
        hex_pub = pub

    for n in range(_c.SOFT_CAP):
        _c.post_listing(_Ident, nsec, title=f"item {n}", description="")
    with pytest.raises(_c.SoftCapExceeded):
        _c.post_listing(_Ident, nsec, title="one too many", description="")
    _c.post_listing(_Ident, nsec, force=True, title="forced", description="")


def test_find_matches_pairs_opposite_sides():
    me = _pub()
    them = _pub()
    my_want = summarize(build_listing_event(me, "Need firewood", "", side="looking-for",
                                            hashtags=["oak"]))
    my_offer = summarize(build_listing_event(me, "Eggs", "", side="offering"))
    their_offer = summarize(build_listing_event(them, "Seasoned oak firewood", "", side="offering"))
    their_want = summarize(build_listing_event(them, "Fresh eggs wanted", "", side="looking-for"))
    unrelated = summarize(build_listing_event(them, "Bicycle", "", side="offering"))
    my_own_offer = summarize(build_listing_event(me, "Firewood", "", side="offering"))
    expired = summarize(build_listing_event(them, "Firewood cheap", "", ttl_days=-1))

    m = _c.find_matches([my_want, my_offer],
                        [their_offer, their_want, unrelated, my_own_offer, expired])
    pairs = {(x["mine"]["title"], x["theirs"]["title"]) for x in m}
    assert pairs == {("Need firewood", "Seasoned oak firewood"), ("Eggs", "Fresh eggs wanted")}
    best = m[0]
    assert best["theirs"]["title"] == "Seasoned oak firewood"  # shares firewood + oak
    assert best["shared"] == ["firewood", "oak"]
