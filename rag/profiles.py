"""Display names from Nostr profiles (kind 0), for showing "from Rosa" instead
of a hex key. Profiles travel between towns with pergamon sync."""
from __future__ import annotations

import asyncio
import json

from rag.nostr import Event, afetch_events


def names_from_events(events: list[Event]) -> dict[str, str]:
    latest: dict[str, Event] = {}
    for ev in events:
        if ev.kind == 0 and (ev.pubkey not in latest or ev.created_at > latest[ev.pubkey].created_at):
            latest[ev.pubkey] = ev
    out = {}
    for pub, ev in latest.items():
        try:
            meta = json.loads(ev.content)
        except ValueError:
            continue
        name = (meta.get("display_name") or meta.get("name") or "").strip() if isinstance(meta, dict) else ""
        if name:
            out[pub] = name[:40]
    return out


async def afetch_names(relays: list[str], pubkeys) -> dict[str, str]:
    pubkeys = sorted({p for p in pubkeys if p})
    if not pubkeys or not relays:
        return {}
    events = await afetch_events(relays, {"kinds": [0], "authors": pubkeys,
                                          "limit": len(pubkeys) * 3}, len(pubkeys) * 3)
    return names_from_events(events)


def fetch_names(relays: list[str], pubkeys) -> dict[str, str]:
    return asyncio.run(afetch_names(relays, pubkeys))


def label(names: dict[str, str], pubkey: str) -> str:
    return names.get(pubkey) or (pubkey[:8] + "…")
