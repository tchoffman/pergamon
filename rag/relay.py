"""Pergamon's own Nostr relay: where board posts, listings, skills, receipts,
profiles and sealed messages are kept, with no Docker needed.

A standard NIP-01 relay over WebSocket, stored in SQLite inside the library
folder (rag/storage.py), so it follows the library onto an external drive.
`pergamon web` starts it on the configured local relay address (default
ws://localhost:8091/) unless something is already serving there; phones and
other hubs reach it on the same port.

- Every event's signature is checked before it's stored.
- Replaceable kinds (0, 3, 10000-19999) keep only the newest per author;
  addressable kinds (30000-39999) keep the newest per author and "d" tag, so
  an edited listing replaces the old one. Ephemeral kinds (20000-29999) are
  passed to live subscribers and not stored.
- The host's moderation list applies here too: blocked keys can't store
  anything, and hidden events aren't served, even to other Nostr apps.

    pergamon relay [--port 8091]
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

from rag.nostr import Event, verify_event

DEFAULT_PORT = 8091
MAX_MESSAGE = 512 * 1024        # sealed messages can be ~90 KB
MAX_LIMIT = 5000
MAX_SUBS = 20                   # per connection
FUTURE_SLACK_S = 15 * 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, pubkey TEXT NOT NULL, kind INTEGER NOT NULL,
  created_at INTEGER NOT NULL, raw TEXT NOT NULL, d_tag TEXT);
CREATE INDEX IF NOT EXISTS ev_kind_time ON events(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS ev_author ON events(pubkey, kind);
CREATE TABLE IF NOT EXISTS tags (
  event_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tag_lookup ON tags(name, value);
CREATE INDEX IF NOT EXISTS tag_event ON tags(event_id);
"""


def default_path() -> Path:
    from rag.storage import data_dir
    return data_dir() / "relay" / "events.db"


def _replaceable(kind: int) -> bool:
    return kind in (0, 3) or 10000 <= kind < 20000


def _addressable(kind: int) -> bool:
    return 30000 <= kind < 40000


def _ephemeral(kind: int) -> bool:
    return 20000 <= kind < 30000


def matches(ev: dict, f: dict) -> bool:
    if "ids" in f and ev["id"] not in f["ids"]:
        return False
    if "kinds" in f and ev["kind"] not in f["kinds"]:
        return False
    if "authors" in f and ev["pubkey"] not in f["authors"]:
        return False
    if "since" in f and ev["created_at"] < f["since"]:
        return False
    if "until" in f and ev["created_at"] > f["until"]:
        return False
    for key, vals in f.items():
        if key.startswith("#") and len(key) == 2:
            have = {t[1] for t in ev["tags"] if len(t) > 1 and t[0] == key[1]}
            if not have & set(vals):
                return False
    return True


class Store:
    """SQLite event store. One connection per call keeps it thread-safe."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def add(self, ev: dict) -> tuple[bool, str]:
        """(stored, message) in NIP-01 OK terms."""
        kind = ev["kind"]
        d = next((t[1] for t in ev["tags"] if len(t) > 1 and t[0] == "d"), "") if _addressable(kind) else None
        with self._lock, self._db() as db:
            if db.execute("SELECT 1 FROM events WHERE id=?", (ev["id"],)).fetchone():
                return True, "duplicate: already have it"
            if _replaceable(kind) or _addressable(kind):
                q = "SELECT id, created_at FROM events WHERE pubkey=? AND kind=?"
                args: list = [ev["pubkey"], kind]
                if d is not None:
                    q += " AND d_tag=?"
                    args.append(d)
                old = db.execute(q, args).fetchall()
                if any(c > ev["created_at"] or (c == ev["created_at"] and i < ev["id"]) for i, c in old):
                    return False, "duplicate: a newer version is already stored"
                for i, _ in old:
                    db.execute("DELETE FROM events WHERE id=?", (i,))
                    db.execute("DELETE FROM tags WHERE event_id=?", (i,))
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                       (ev["id"], ev["pubkey"], kind, ev["created_at"],
                        json.dumps(ev, separators=(",", ":"), ensure_ascii=False), d))
            db.executemany("INSERT INTO tags VALUES (?,?,?)",
                           [(ev["id"], t[0], t[1]) for t in ev["tags"]
                            if len(t) > 1 and len(t[0]) == 1 and isinstance(t[1], str)])
        return True, ""

    def query(self, f: dict) -> list[dict]:
        where, args = [], []
        for col, key in (("id", "ids"), ("pubkey", "authors"), ("kind", "kinds")):
            if key in f:
                vals = list(f[key])[:1000]
                if not vals:
                    return []
                where.append(f"{col} IN ({','.join('?' * len(vals))})")
                args += vals
        if "since" in f:
            where.append("created_at >= ?"); args.append(int(f["since"]))
        if "until" in f:
            where.append("created_at <= ?"); args.append(int(f["until"]))
        for key, vals in f.items():
            if key.startswith("#") and len(key) == 2:
                vals = list(vals)[:1000]
                if not vals:
                    return []
                where.append(f"id IN (SELECT event_id FROM tags WHERE name=? AND value IN "
                             f"({','.join('?' * len(vals))}))")
                args += [key[1], *vals]
        limit = min(int(f.get("limit", 500)), MAX_LIMIT)
        sql = "SELECT raw FROM events" + (" WHERE " + " AND ".join(where) if where else "") + \
              " ORDER BY created_at DESC, id LIMIT ?"
        with self._db() as db:
            return [json.loads(r[0]) for r in db.execute(sql, [*args, limit])]

    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def _event_from(raw: dict) -> Event:
    return Event(kind=int(raw["kind"]), content=str(raw["content"]), pubkey=str(raw["pubkey"]),
                 tags=[list(map(str, t)) for t in raw["tags"]], created_at=int(raw["created_at"]),
                 id=str(raw["id"]), sig=str(raw["sig"]))


class Relay:
    def __init__(self, path: Path | None = None):
        self.store = Store(path or default_path())
        self._subs: dict = {}          # websocket → {sub_id: filters}

    def _visible(self, raw: dict) -> bool:
        from rag import moderation
        try:
            return moderation.allowed(_event_from(raw))
        except Exception:  # noqa: BLE001
            return True

    async def _on_event(self, ws, raw) -> None:
        try:
            ev = _event_from(raw)
        except (KeyError, TypeError, ValueError):
            await ws.send(json.dumps(["NOTICE", "invalid: malformed event"]))
            return
        if not verify_event(ev):
            await ws.send(json.dumps(["OK", ev.id, False, "invalid: bad signature"]))
            return
        if ev.created_at > time.time() + FUTURE_SLACK_S:
            await ws.send(json.dumps(["OK", ev.id, False, "invalid: created_at is in the future"]))
            return
        from rag import moderation
        if moderation.is_blocked(ev.pubkey):
            await ws.send(json.dumps(["OK", ev.id, False, "blocked: this hub's host blocked this key"]))
            return
        clean = ev.to_json()
        if _ephemeral(ev.kind):
            ok, msg = True, ""
        else:
            ok, msg = await asyncio.to_thread(self.store.add, clean)
        await ws.send(json.dumps(["OK", ev.id, ok, msg]))
        if ok and not msg.startswith("duplicate"):
            await self._broadcast(clean)

    async def _broadcast(self, ev: dict) -> None:
        if not self._visible(ev):
            return
        for ws, subs in list(self._subs.items()):
            for sub_id, filters in list(subs.items()):
                if any(matches(ev, f) for f in filters):
                    try:
                        await ws.send(json.dumps(["EVENT", sub_id, ev]))
                    except Exception:  # noqa: BLE001
                        pass
                    break

    async def _on_req(self, ws, sub_id, filters) -> None:
        subs = self._subs.setdefault(ws, {})
        if sub_id not in subs and len(subs) >= MAX_SUBS:
            await ws.send(json.dumps(["CLOSED", sub_id, "error: too many subscriptions"]))
            return
        filters = [f for f in filters if isinstance(f, dict)]
        subs[sub_id] = filters
        seen = set()
        for f in filters:
            for ev in await asyncio.to_thread(self.store.query, f):
                if ev["id"] not in seen and self._visible(ev):
                    seen.add(ev["id"])
                    await ws.send(json.dumps(["EVENT", sub_id, ev]))
        await ws.send(json.dumps(["EOSE", sub_id]))

    async def handler(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, list) or not msg:
                        raise ValueError
                    if msg[0] == "EVENT" and len(msg) >= 2:
                        await self._on_event(ws, msg[1])
                    elif msg[0] == "REQ" and len(msg) >= 3:
                        await self._on_req(ws, str(msg[1])[:64], msg[2:])
                    elif msg[0] == "CLOSE" and len(msg) >= 2:
                        self._subs.get(ws, {}).pop(msg[1], None)
                    else:
                        await ws.send(json.dumps(["NOTICE", "unknown message"]))
                except (ValueError, TypeError):
                    await ws.send(json.dumps(["NOTICE", "invalid: not a Nostr message"]))
        except Exception:  # noqa: BLE001 — a dropped connection is normal
            pass
        finally:
            self._subs.pop(ws, None)

    async def serve(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT, ready=None) -> None:
        from websockets.asyncio.server import serve
        async with serve(self.handler, host, port, max_size=MAX_MESSAGE) as server:
            if ready:
                ready(server)
            await server.serve_forever()


def local_port() -> int | None:
    """The port of the configured relay if it's on this machine, else None."""
    from urllib.parse import urlparse
    from rag.nostr import _load_relays_from_config
    for url in _load_relays_from_config():
        u = urlparse(url)
        if u.hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return u.port or 80
    return None


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str]) -> int:
    port = DEFAULT_PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    relay = Relay()
    print(f"Pergamon relay on ws://0.0.0.0:{port}/  ({relay.store.count()} events in {relay.store.path})")
    try:
        asyncio.run(relay.serve(port=port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
