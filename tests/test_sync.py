from __future__ import annotations

import asyncio
import json

from rag import sync
from rag.board import build_post
from rag.classifieds import build_listing_event
from rag.identity import generate_keypair
from rag.nostr import sign_event


def _events():
    nsec, _, pub = generate_keypair()
    return [
        sign_event(build_post(pub, "Bridge out", "news"), nsec),
        sign_event(build_listing_event(pub, "Eggs", "3 dozen"), nsec),
    ]


def test_bundle_round_trip(tmp_path):
    evs = _events()
    p = tmp_path / "b.jsonl"
    assert sync.write_bundle(evs, p) == 2
    back, bad = sync.read_bundle(p)
    assert bad == 0
    assert {e.id for e in back} == {e.id for e in evs}


def test_bundle_rejects_tampered_and_garbage(tmp_path):
    evs = _events()
    p = tmp_path / "b.jsonl"
    sync.write_bundle(evs, p)
    lines = p.read_text().splitlines()
    forged = json.loads(lines[0])
    forged["content"] = "Bridge is fine, come on through"
    lines[0] = json.dumps(forged)
    lines.append("not json")
    p.write_text("\n".join(lines) + "\n")
    back, bad = sync.read_bundle(p)
    assert bad == 2
    assert len(back) == 1


def test_sync_filters_cover_board_and_catalog_kinds():
    fs = sync.sync_filters(since=123)
    assert fs[0]["kinds"] == [1] and fs[0]["#t"] == ["pergamon-board"]
    assert {0, 30402, 30405, 30406, 30407} <= set(fs[1]["kinds"])
    assert all(f["since"] == 123 for f in fs)


class _FakeWS:
    def __init__(self, dup_ids=()):
        self.sent = []
        self.dup = set(dup_ids)
        self._queue = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        eid = msg[1]["id"]
        if eid in self.dup:
            self._queue.append(json.dumps(["OK", eid, False, "duplicate: have it"]))
        else:
            self._queue.append(json.dumps(["OK", eid, True, ""]))

    async def recv(self):
        return self._queue.pop(0)


def test_publish_batch_counts_duplicates_as_stored(monkeypatch):
    evs = _events()
    fake = _FakeWS(dup_ids={evs[0].id})
    monkeypatch.setattr(sync.websockets, "connect", lambda *a, **k: fake)
    ok, rej, errs = asyncio.run(sync.publish_batch("ws://fake/", evs))
    assert (ok, rej, errs) == (2, 0, [])
    assert len(fake.sent) == 2


def test_publish_batch_reports_connection_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(sync.websockets, "connect", boom)
    ok, rej, errs = asyncio.run(sync.publish_batch("ws://x/", _events()))
    assert ok == 0 and errs
