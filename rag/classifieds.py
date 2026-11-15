"""Classifieds — NIP-99 addressable "classified listing" events (kind 30402).

Standard tags per NIP-99:
    ["d", unique-id]            (required for addressable kinds)
    ["title", text]
    ["summary", text]
    ["price", amount, currency]
    ["location", text]
    ["t", hashtag]              (repeated)
    ["published_at", unix_ts]
    ["status", "active" | "sold"]
    ["image", url]              (repeated; optional)

Pergamon extensions (used alongside the standard tags, not in place of them):
    ["price", amount, "oz_ag"]   — our junk-silver unit of account
    ["accepts", "silver,barter,labor,cash,kwh"]
    ["pergamon:hosted-by", host_npub]   — when posted via a host on behalf

CLI:
    pergamon classified post --title "20lb apples" --price-oz-ag 0.3 \\
        --accepts silver,barter -t apples -t harvest "Honeycrisp, pick-up only"

    pergamon classified list                                  # top 50 recent
    pergamon classified list --tag apples
    pergamon classified search "chicken"                      # client-side filter
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any

import websockets
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from rag.config import REPO_ROOT
from rag.identity import get_active, load_nsec
from rag.nostr import (
    Event,
    _load_relays_from_config,
    build_event,
    publish,
    sign_event,
    verify_event,
)

console = Console()

KIND_CLASSIFIED = 30402
DEFAULT_LIMIT = 50
DEFAULT_TTL_DAYS = 14
SOFT_CAP = 10
SIDES = ("offering", "looking-for")
MINE_PATH = REPO_ROOT / "build" / "classifieds" / "mine.json"


# ────────────────────────── build + post ────────────────────────────

def build_listing_event(
    pubkey_hex: str,
    title: str,
    description: str,
    *,
    price_oz_ag: float | None = None,
    price_usd: float | None = None,
    accepts: list[str] | None = None,
    location: str | None = None,
    summary: str | None = None,
    hashtags: list[str] | None = None,
    status: str = "active",
    listing_id: str | None = None,
    image_urls: list[str] | None = None,
    side: str = "offering",
    ttl_days: float = DEFAULT_TTL_DAYS,
    qty: str | None = None,
) -> Event:
    """Build an unsigned kind-30402 event. Caller signs + publishes."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    tags: list[list[str]] = []
    listing_id = listing_id or secrets.token_hex(8)
    tags.append(["d", listing_id])
    tags.append(["title", title])
    tags.append(["side", side])
    tags.append(["expires_at", str(int(time.time() + ttl_days * 86400))])
    if qty:
        tags.append(["qty", qty])
    if summary:
        tags.append(["summary", summary])
    if price_oz_ag is not None:
        tags.append(["price", str(price_oz_ag), "oz_ag"])
    if price_usd is not None:
        tags.append(["price", str(price_usd), "USD"])
    if accepts:
        tags.append(["accepts", ",".join(accepts)])
    if location:
        tags.append(["location", location])
    tags.append(["published_at", str(int(time.time()))])
    tags.append(["status", status])
    for h in hashtags or []:
        tags.append(["t", h])
    for u in image_urls or []:
        tags.append(["image", u])

    return build_event(kind=KIND_CLASSIFIED, content=description, pubkey_hex=pubkey_hex, tags=tags)


def summarize(event: Event) -> dict:
    """Pull the useful fields out of a classified event for display / filtering."""
    tags = {t[0]: t[1:] for t in event.tags}  # last-wins on dupe keys; fine for our fields
    out = {
        "id": event.id,
        "pubkey": event.pubkey,
        "created_at": event.created_at,
        "d": tags.get("d", [""])[0],
        "title": tags.get("title", [""])[0],
        "summary": tags.get("summary", [""])[0],
        "location": tags.get("location", [""])[0],
        "status": tags.get("status", ["active"])[0],
        "description": event.content,
        "hashtags": [t[1] for t in event.tags if t[0] == "t" and len(t) >= 2],
        "accepts": [],
        "price_oz_ag": None,
        "price_usd": None,
        "side": tags.get("side", ["offering"])[0],
        "qty": tags.get("qty", [""])[0],
        "expires_at": None,
    }
    try:
        out["expires_at"] = int(tags["expires_at"][0])
    except (KeyError, IndexError, ValueError):
        pass
    out["is_live"] = is_live(out)
    for t in event.tags:
        if t[0] == "price" and len(t) >= 3:
            try:
                amount = float(t[1])
            except ValueError:
                continue
            if t[2] == "oz_ag":
                out["price_oz_ag"] = amount
            elif t[2] == "USD":
                out["price_usd"] = amount
        elif t[0] == "accepts" and len(t) >= 2:
            out["accepts"] = [x.strip() for x in t[1].split(",") if x.strip()]
    return out


def is_live(info: dict, now: float | None = None) -> bool:
    """Active status and not past expires_at. Listings with no expiry (posted
    before TTLs existed) are treated as expired after DEFAULT_TTL_DAYS."""
    if info.get("status") != "active":
        return False
    now = now if now is not None else time.time()
    exp = info.get("expires_at")
    if exp is None:
        exp = info.get("created_at", 0) + DEFAULT_TTL_DAYS * 86400
    return now < exp


def latest_per_listing(events: list[Event]) -> list[Event]:
    """NIP-33 replaceable semantics: keep the newest event per (pubkey, d)."""
    best: dict[tuple[str, str], Event] = {}
    for ev in events:
        d = next((t[1] for t in ev.tags if t and t[0] == "d" and len(t) > 1), ev.id)
        key = (ev.pubkey, d)
        if key not in best or ev.created_at > best[key].created_at:
            best[key] = ev
    return sorted(best.values(), key=lambda e: e.created_at, reverse=True)


# ───────────────────────── local copy of my listings ─────────────────

def _load_mine() -> dict[str, dict]:
    if not MINE_PATH.exists():
        return {}
    return json.loads(MINE_PATH.read_text())


def _save_mine(mine: dict[str, dict]) -> None:
    MINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MINE_PATH.write_text(json.dumps(mine, indent=2))


def _remember(ev: Event) -> None:
    mine = _load_mine()
    d = next(t[1] for t in ev.tags if t[0] == "d")
    mine[d] = ev.to_json()
    _save_mine(mine)


def my_live_listings(pubkey_hex: str) -> list[dict]:
    out = []
    for raw in _load_mine().values():
        if raw["pubkey"] != pubkey_hex:
            continue
        info = summarize(_event_from_json(raw))
        if info["is_live"]:
            out.append(info)
    return out


def _event_from_json(raw: dict) -> Event:
    return Event(
        kind=raw["kind"], content=raw["content"], pubkey=raw["pubkey"],
        tags=raw.get("tags", []), created_at=raw["created_at"],
        id=raw.get("id", ""), sig=raw.get("sig", ""),
    )


def republish(d: str, nsec: str, *, status: str | None = None,
              ttl_days: float = DEFAULT_TTL_DAYS) -> Event:
    """Re-sign a remembered listing with a fresh expiry (renew) or new status
    (close). Same d-tag, so relays and clients replace the old version."""
    mine = _load_mine()
    if d not in mine:
        raise KeyError(f"no local listing with id {d!r}")
    ev = _event_from_json(mine[d])
    now = int(time.time())
    new_tags = []
    for t in ev.tags:
        if t[0] == "expires_at":
            continue
        if t[0] == "status" and status:
            continue
        if t[0] == "published_at":
            continue
        new_tags.append(t)
    new_tags.append(["expires_at", str(int(now + ttl_days * 86400))])
    new_tags.append(["published_at", str(now)])
    if status:
        new_tags.append(["status", status])
    fresh = build_event(kind=ev.kind, content=ev.content, pubkey_hex=ev.pubkey, tags=new_tags)
    sign_event(fresh, nsec)
    return fresh


# ───────────────────────── fetch from relays ───────────────────────

async def _subscribe_and_collect(
    relay_url: str,
    filters: dict,
    max_events: int,
    timeout: float = 10.0,
) -> list[Event]:
    """Open a REQ subscription on one relay, collect events until EOSE or max_events."""
    events: list[Event] = []
    sub_id = secrets.token_hex(4)
    try:
        async with websockets.connect(relay_url, open_timeout=timeout) as ws:
            await ws.send(json.dumps(["REQ", sub_id, filters]))
            while len(events) < max_events:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if not isinstance(msg, list) or not msg:
                    continue
                if msg[0] == "EVENT" and len(msg) >= 3 and msg[1] == sub_id:
                    raw_event = msg[2]
                    ev = Event(
                        kind=raw_event["kind"],
                        content=raw_event["content"],
                        pubkey=raw_event["pubkey"],
                        tags=raw_event.get("tags", []),
                        created_at=raw_event["created_at"],
                        id=raw_event["id"],
                        sig=raw_event["sig"],
                    )
                    if verify_event(ev):
                        events.append(ev)
                elif msg[0] == "EOSE" and len(msg) >= 2 and msg[1] == sub_id:
                    break
            await ws.send(json.dumps(["CLOSE", sub_id]))
    except Exception as e:
        console.print(f"[yellow]relay {relay_url} error:[/yellow] {e}")
    return events


async def afetch_listings(
    relay_urls: list[str],
    tag: str | None = None,
    limit: int = DEFAULT_LIMIT,
    live_only: bool = True,
) -> list[Event]:
    """Pull classified events from relays; newest version per listing wins."""
    filters: dict = {"kinds": [KIND_CLASSIFIED], "limit": limit}
    if tag:
        filters["#t"] = [tag]
    batches = await asyncio.gather(
        *(_subscribe_and_collect(u, filters, limit) for u in relay_urls)
    )
    events = latest_per_listing([ev for b in batches for ev in b])
    if live_only:
        events = [ev for ev in events if summarize(ev)["is_live"]]
    return events


def fetch_listings(relay_urls: list[str], tag: str | None = None,
                   limit: int = DEFAULT_LIMIT, live_only: bool = True) -> list[Event]:
    return asyncio.run(afetch_listings(relay_urls, tag, limit, live_only))


def search_infos(events: list[Event], query: str = "", side: str | None = None) -> list[dict]:
    query = query.lower().strip()
    out = []
    for ev in events:
        info = summarize(ev)
        if side and info["side"] != side:
            continue
        if query:
            hay = " ".join([info["title"], info["description"], info["location"],
                            " ".join(info["hashtags"])]).lower()
            if query not in hay:
                continue
        out.append(info)
    return out


# ────────────────────────────── CLI ────────────────────────────────

def _print_listing(info: dict) -> None:
    price_parts = []
    if info.get("price_oz_ag") is not None:
        price_parts.append(f"{info['price_oz_ag']} oz Ag")
    if info.get("price_usd") is not None:
        price_parts.append(f"${info['price_usd']}")
    if not price_parts:
        price_parts.append("barter")
    side = "WANTED" if info.get("side") == "looking-for" else "OFFER"
    head = f"[bold]{side}: {info['title'] or '(no title)'}[/bold]  —  {' · '.join(price_parts)}"
    body_lines = []
    if info.get("qty"):
        body_lines.append(f"[dim]qty:[/dim] {info['qty']}")
    if info.get("expires_at"):
        days = (info["expires_at"] - time.time()) / 86400
        body_lines.append(f"[dim]expires:[/dim] {'in %.0f days' % days if days > 0 else 'expired'}"
                          f"  [dim]id:[/dim] {info['d']}")
    if info.get("location"):
        body_lines.append(f"[dim]location:[/dim] {info['location']}")
    if info.get("accepts"):
        body_lines.append(f"[dim]accepts:[/dim] {', '.join(info['accepts'])}")
    if info.get("hashtags"):
        body_lines.append(f"[dim]tags:[/dim] {', '.join('#' + t for t in info['hashtags'])}")
    body_lines.append(f"[dim]status:[/dim] {info['status']}  [dim]from:[/dim] {info['pubkey'][:16]}…")
    body_lines.append("")
    body_lines.append(info["description"])
    console.print(Panel("\n".join(body_lines), title=head, border_style="cyan"))


class SoftCapExceeded(Exception):
    pass


def post_listing(ident, nsec: str, *, force: bool = False, **fields) -> tuple[Event, dict]:
    """Build, sign, publish, and remember a listing. Shared by CLI + web.

    Raises SoftCapExceeded when the identity already has SOFT_CAP live
    listings and force is False.
    """
    if not force and len(my_live_listings(ident.hex_pub)) >= SOFT_CAP:
        raise SoftCapExceeded(
            f"you already have {SOFT_CAP} live listings — close or let one expire, "
            "or pass force to override"
        )
    ev = build_listing_event(pubkey_hex=ident.hex_pub, **fields)
    sign_event(ev, nsec)
    results = publish(ev, _load_relays_from_config())
    _remember(ev)
    return ev, results


def _report(results: dict) -> bool:
    any_ok = False
    for url, (ok, msg) in results.items():
        mark = "[green]ok[/green]" if ok else "[red]fail[/red]"
        console.print(f"  [{mark}] {url}  {msg}")
        any_ok = any_ok or ok
    return any_ok


def _active_or_fail():
    ident = get_active()
    if not ident:
        console.print("[red]no active identity.[/red] Create one: pergamon identity create <name>")
        return None, None
    return ident, load_nsec(ident.name)


def _cmd_post(argv: list[str]) -> int:
    fields: dict = {"hashtags": []}
    description_parts: list[str] = []
    force = False
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--title" and nxt is not None:
            fields["title"] = nxt; i += 2
        elif a == "--price-oz-ag" and nxt is not None:
            fields["price_oz_ag"] = float(nxt); i += 2
        elif a == "--price-usd" and nxt is not None:
            fields["price_usd"] = float(nxt); i += 2
        elif a == "--accepts" and nxt is not None:
            fields["accepts"] = [x.strip() for x in nxt.split(",") if x.strip()]; i += 2
        elif a == "--location" and nxt is not None:
            fields["location"] = nxt; i += 2
        elif a == "--qty" and nxt is not None:
            fields["qty"] = nxt; i += 2
        elif a == "--ttl" and nxt is not None:
            fields["ttl_days"] = float(nxt.rstrip("d")); i += 2
        elif a == "--want":
            fields["side"] = "looking-for"; i += 1
        elif a == "--force":
            force = True; i += 1
        elif a in ("-t", "--tag") and nxt is not None:
            fields["hashtags"].append(nxt); i += 2
        else:
            description_parts.append(a); i += 1
    if not fields.get("title"):
        console.print(
            "Usage: pergamon classified post --title \"...\" [--want] [--price-oz-ag N] "
            "[--accepts a,b] [--qty \"20 lb\"] [--ttl 14d] [-t tag] \"description\""
        )
        return 2
    fields["description"] = " ".join(description_parts).strip()

    ident, nsec = _active_or_fail()
    if not ident:
        return 1
    try:
        ev, results = post_listing(ident, nsec, force=force, **fields)
    except SoftCapExceeded as e:
        console.print(f"[yellow]{e}[/yellow]")
        return 1
    ok = _report(results)
    d = next(t[1] for t in ev.tags if t[0] == "d")
    console.print(f"\nlisting id: [cyan]{d}[/cyan]  (renew with: pergamon classified renew {d})")
    return 0 if ok else 1


def _cmd_republish(argv: list[str], status: str | None) -> int:
    if not argv:
        console.print(f"Usage: pergamon classified {'close' if status else 'renew'} <listing-id>")
        return 2
    ident, nsec = _active_or_fail()
    if not ident:
        return 1
    try:
        ev = republish(argv[0], nsec, status=status)
    except KeyError as e:
        console.print(f"[red]{e}[/red] — see: pergamon classified mine")
        return 1
    ok = _report(publish(ev, _load_relays_from_config()))
    _remember(ev)
    console.print(f"[green]{'closed' if status else 'renewed'}[/green] {argv[0]}")
    return 0 if ok else 1


def _cmd_mine(argv: list[str]) -> int:
    ident, _ = _active_or_fail()
    if not ident:
        return 1
    rows = [summarize(_event_from_json(r)) for r in _load_mine().values()
            if r["pubkey"] == ident.hex_pub]
    if not rows:
        console.print("[yellow]you have no listings yet[/yellow]")
        return 0
    table = Table(title=f"Listings by {ident.name}")
    for c in ("id", "side", "title", "status", "expires"):
        table.add_column(c)
    for r in sorted(rows, key=lambda x: x["created_at"], reverse=True):
        if r["status"] != "active":
            exp = r["status"]
        elif not r["is_live"]:
            exp = "[red]expired[/red]"
        else:
            exp = f"{(r['expires_at'] - time.time()) / 86400:.0f}d"
        table.add_row(r["d"], r["side"], r["title"], r["status"], exp)
    console.print(table)
    live = sum(1 for r in rows if r["is_live"])
    console.print(f"[dim]{live}/{SOFT_CAP} live listings[/dim]")
    return 0


def _parse_list_flags(argv: list[str]) -> dict:
    opts = {"tag": None, "limit": DEFAULT_LIMIT, "side": None, "live_only": True}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--tag" and i + 1 < len(argv):
            opts["tag"] = argv[i + 1]; i += 2
        elif a == "--limit" and i + 1 < len(argv):
            opts["limit"] = int(argv[i + 1]); i += 2
        elif a == "--wanted":
            opts["side"] = "looking-for"; i += 1
        elif a == "--offers":
            opts["side"] = "offering"; i += 1
        elif a == "--all":
            opts["live_only"] = False; i += 1
        else:
            raise ValueError(f"unknown flag: {a}")
    return opts


def _cmd_list(argv: list[str]) -> int:
    try:
        opts = _parse_list_flags(argv)
    except ValueError as e:
        console.print(str(e))
        return 2
    relays = _load_relays_from_config()
    events = fetch_listings(relays, tag=opts["tag"], limit=opts["limit"],
                            live_only=opts["live_only"])
    infos = search_infos(events, side=opts["side"])
    if not infos:
        console.print("[yellow]no live classifieds found.[/yellow] (--all includes expired)")
        return 0
    for info in infos:
        _print_listing(info)
    console.print(f"\n[dim]{len(infos)} listing(s) from {len(relays)} relay(s)[/dim]")
    return 0


def _cmd_search(argv: list[str]) -> int:
    if not argv:
        console.print("Usage: pergamon classified search \"query\"")
        return 2
    query = " ".join(argv)
    relays = _load_relays_from_config()
    hits = search_infos(fetch_listings(relays, limit=200), query=query)
    if not hits:
        console.print(f"[yellow]no matches for {query!r}[/yellow]")
        return 0
    for h in hits:
        _print_listing(h)
    console.print(f"\n[dim]{len(hits)} match(es)[/dim]")
    return 0


USAGE = """Usage:
  pergamon classified post --title "..." [--want] [--price-oz-ag N] [--price-usd N]
                           [--accepts a,b] [--qty "20 lb"] [--location X] [--ttl 14d]
                           [-t tag] [--force] "description"
  pergamon classified list [--offers|--wanted] [--tag X] [--limit N] [--all]
  pergamon classified search "query"
  pergamon classified mine                 your listings + expiry
  pergamon classified renew <id>           push expiry out another 14 days
  pergamon classified close <id>           mark sold / no longer wanted"""


def main(argv: list[str]) -> int:
    if not argv:
        console.print(USAGE)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "post":   return _cmd_post(rest)
    if cmd == "list":   return _cmd_list(rest)
    if cmd == "search": return _cmd_search(rest)
    if cmd == "mine":   return _cmd_mine(rest)
    if cmd == "renew":  return _cmd_republish(rest, status=None)
    if cmd == "close":  return _cmd_republish(rest, status="sold")
    console.print(f"unknown command: {cmd}\n{USAGE}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
