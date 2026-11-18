"""A tiny in-memory NIP-01 relay for integration tests.

Supports EVENT (with OK), REQ (kinds, authors, #t, since, until, limit) with
EOSE, and CLOSE. Runs on its own event loop in a background thread so code
under test can call asyncio.run() freely.
"""
from __future__ import annotations

import asyncio
import json
import threading

import websockets


def _matches(ev: dict, f: dict) -> bool:
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
            name = key[1]
            have = {t[1] for t in ev["tags"] if len(t) > 1 and t[0] == name}
            if not have & set(vals):
                return False
    return True


class MemoryRelay:
    def __init__(self, port: int = 0):
        self.events: dict[str, dict] = {}
        self._want_port = port
        self.loop = asyncio.new_event_loop()
        self.port = None
        self._server = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/"

    async def _handler(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "EVENT":
                ev = msg[1]
                dup = ev["id"] in self.events
                self.events[ev["id"]] = ev
                await ws.send(json.dumps(["OK", ev["id"], not dup,
                                          "duplicate: already have it" if dup else ""]))
            elif msg[0] == "REQ":
                sub, filters = msg[1], msg[2:]
                out = sorted(self.events.values(), key=lambda e: e["created_at"], reverse=True)
                for f in filters:
                    hits = [e for e in out if _matches(e, f)][: f.get("limit", 500)]
                    for e in hits:
                        await ws.send(json.dumps(["EVENT", sub, e]))
                await ws.send(json.dumps(["EOSE", sub]))

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self._server = await websockets.serve(self._handler, "127.0.0.1", self._want_port)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        self.loop.run_until_complete(start())
        self.loop.run_forever()

    def start(self) -> "MemoryRelay":
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self):
        async def shutdown():
            self._server.close()
            await self._server.wait_closed()
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


class MiniRelay(MemoryRelay):
    """Pergamon's real relay (rag/relay.py) on a throwaway SQLite file, with the
    same start/stop/url/events interface, so every integration test exercises
    the relay the hub actually runs."""

    def __init__(self, port: int = 0):
        super().__init__(port)
        import tempfile
        from pathlib import Path
        from rag.relay import Relay
        self._dir = tempfile.TemporaryDirectory()
        self.relay = Relay(Path(self._dir.name) / "events.db")

    @property
    def events(self) -> dict:
        return {e["id"]: e for e in self.relay.store.query({"limit": 5000})}

    @events.setter
    def events(self, _value) -> None:  # the parent's in-memory store isn't used
        pass

    async def _handler(self, ws):
        await self.relay.handler(ws)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            from rag.relay import MAX_MESSAGE
            self._server = await websockets.serve(self._handler, "127.0.0.1", self._want_port,
                                                  max_size=MAX_MESSAGE)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()

        self.loop.run_until_complete(start())
        self.loop.run_forever()

    def stop(self):
        super().stop()
        self._dir.cleanup()
