"""Move the community's events between pergamons without the internet.

Two transports, one format (signed Nostr events):

  Relay-to-relay gossip — whenever two pergamons can reach each other at all
  (LAN, long-range WiFi, a laptop someone carries over), copy events across:
      pergamon sync pull ws://other-pergamon:8091/
      pergamon sync push ws://other-pergamon:8091/
      pergamon sync both ws://other-pergamon:8091/

  Sneakernet — write the events to a file, carry it on a USB stick, load it
  into the next town's pergamon:
      pergamon sync export --path /Volumes/USB/pergamon-2027-03-01.jsonl
      pergamon sync import /Volumes/USB/pergamon-2027-03-01.jsonl

Every event is signature-checked on import and on fetch, so a courier can
carry a bundle without being able to alter or forge anyone's posts. Relays
de-duplicate by event id, so syncing the same data twice is harmless.

What gets synced: community-board posts, classifieds, trade receipts,
library catalogs, skill/service listings, profiles, and gift-wrapped private
messages (NIP-17), so a letter to someone in the next town travels with the
rest. A wrap names only its recipient; sender, text and true time are inside.
Never the old readable-metadata DMs (kind 4).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets
from rich.console import Console

from rag import moderation
from rag.nostr import (
    Event,
    _load_relays_from_config,
    subscribe_and_collect,
    verify_event,
)

console = Console()

BOARD_TAG = "pergamon-board"
# profiles, classifieds, receipts, catalogs, skills, and gift-wrapped private
# messages (kind 1059: they show only the recipient, never the sender).
SYNC_KINDS = [0, 30402, 30405, 30406, 30407, 1059]
DEFAULT_DAYS = 60
DEFAULT_LIMIT = 5000


def sync_filters(since: int) -> list[dict]:
    return [
        {"kinds": [1], "#t": [BOARD_TAG], "since": since, "limit": DEFAULT_LIMIT},
        {"kinds": SYNC_KINDS, "since": since, "limit": DEFAULT_LIMIT},
    ]


async def collect(relay_url: str, since: int) -> list[Event]:
    seen: dict[str, Event] = {}
    for f in sync_filters(since):
        for ev in await subscribe_and_collect(relay_url, f, f["limit"]):
            seen[ev.id] = ev
    return list(seen.values())


async def publish_batch(relay_url: str, events: list[Event],
                        timeout: float = 15.0) -> tuple[int, int, list[str]]:
    """Send many events over one connection. Returns (accepted, rejected, errors).

    "duplicate:" rejections count as accepted — the relay already had it.
    """
    if not events:
        return 0, 0, []
    pending = {ev.id for ev in events}
    accepted = rejected = 0
    errors: list[str] = []
    try:
        async with websockets.connect(relay_url, open_timeout=timeout,
                                      max_size=2 ** 22) as ws:
            for ev in events:
                await ws.send(json.dumps(["EVENT", ev.to_json()]))
            while pending:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    errors.append(f"timeout with {len(pending)} unacknowledged")
                    break
                msg = json.loads(raw)
                if not (isinstance(msg, list) and msg and msg[0] == "OK" and len(msg) >= 3):
                    continue
                if msg[1] not in pending:
                    continue
                pending.discard(msg[1])
                reason = msg[3] if len(msg) > 3 else ""
                if msg[2] or str(reason).startswith("duplicate"):
                    accepted += 1
                else:
                    rejected += 1
                    if len(errors) < 5:
                        errors.append(str(reason))
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
    return accepted, rejected, errors


def write_bundle(events: list[Event], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in sorted(events, key=lambda e: e.created_at):
            f.write(json.dumps(ev.to_json(), separators=(",", ":")) + "\n")
    return len(events)


def read_bundle(path: Path) -> tuple[list[Event], int]:
    """Return (valid events, number of lines that failed verification)."""
    good: list[Event] = []
    bad = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            ev = Event(kind=raw["kind"], content=raw["content"], pubkey=raw["pubkey"],
                       tags=raw.get("tags", []), created_at=raw["created_at"],
                       id=raw["id"], sig=raw["sig"])
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        if verify_event(ev):
            if moderation.allowed(ev):  # hidden or blocked here: not loaded
                good.append(ev)
        else:
            bad += 1
    return good, bad


# ────────────────────────────── CLI ──────────────────────────────────

def _since(days: float) -> int:
    return int(time.time() - days * 86400)


def _copy(src: str, dst: str, since: int) -> int:
    events = asyncio.run(collect(src, since))
    ok, rej, errs = asyncio.run(publish_batch(dst, events))
    console.print(f"  {src} → {dst}: {len(events)} fetched, {ok} stored, {rej} rejected")
    for e in errs:
        console.print(f"    [yellow]{e}[/yellow]")
    return 0 if not errs or ok else 1


def main(argv: list[str]) -> int:
    usage = (
        "Usage:\n"
        "  pergamon sync pull <ws://remote>   copy remote events into your relay(s)\n"
        "  pergamon sync push <ws://remote>   copy your events to the remote\n"
        "  pergamon sync both <ws://remote>\n"
        "  pergamon sync export --path FILE   write a sneakernet bundle (.jsonl)\n"
        "  pergamon sync import FILE          verify + load a bundle into your relay(s)\n"
        f"  options: --days N (default {DEFAULT_DAYS})"
    )
    if not argv:
        console.print(usage)
        return 2
    cmd, rest = argv[0], argv[1:]
    days = DEFAULT_DAYS
    path = None
    positional: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--days" and i + 1 < len(rest):
            days = float(rest[i + 1]); i += 2
        elif rest[i] == "--path" and i + 1 < len(rest):
            path = Path(rest[i + 1]); i += 2
        else:
            positional.append(rest[i]); i += 1
    since = _since(days)
    local = _load_relays_from_config()

    if cmd in ("pull", "push", "both"):
        if not positional:
            console.print(usage)
            return 2
        remote = positional[0]
        rc = 0
        if cmd in ("pull", "both"):
            for dst in local:
                rc |= _copy(remote, dst, since)
        if cmd in ("push", "both"):
            for src in local:
                rc |= _copy(src, remote, since)
        return rc

    if cmd == "export":
        path = path or Path(f"pergamon-bundle-{time.strftime('%Y%m%d')}.jsonl")
        seen: dict[str, Event] = {}
        for src in local:
            for ev in asyncio.run(collect(src, since)):
                seen[ev.id] = ev
        n = write_bundle(list(seen.values()), path)
        console.print(f"[green]wrote {n} events[/green] → {path}")
        return 0

    if cmd == "import":
        src = path or (Path(positional[0]) if positional else None)
        if not src or not src.exists():
            console.print("[red]bundle file not found[/red]")
            return 2
        events, bad = read_bundle(src)
        console.print(f"{len(events)} valid events in bundle" +
                      (f", [red]{bad} failed verification and were skipped[/red]" if bad else ""))
        rc = 0
        for dst in local:
            ok, rej, errs = asyncio.run(publish_batch(dst, events))
            console.print(f"  → {dst}: {ok} stored, {rej} rejected")
            for e in errs:
                console.print(f"    [yellow]{e}[/yellow]")
            if errs and not ok:
                rc = 1
        return rc

    console.print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
