"""Reputation as plain, checkable facts — not a magic score.

Computed locally from events this pergamon can see (its relays, plus
whatever has arrived by sync). Different pergamons may see different
numbers; that's expected.

    confirmed trades   both parties signed a matching receipt
    trade partners     distinct people among those trades
    listings kept up   of their recent listings, how many were closed or
                       are still live vs. left to lapse while "active"
    active since       their oldest event we've seen

CLI:
    pergamon reputation <npub-or-hex>
"""
from __future__ import annotations

import asyncio
import sys
import time

from rich.console import Console

from rag.classifieds import KIND_CLASSIFIED, latest_per_listing, summarize as summarize_listing
from rag.nostr import _load_relays_from_config, afetch_events
from rag.receipts import _hex_pub, afetch_for, confirmed_receipts

console = Console()

WINDOW_DAYS = 365


def compute_from_events(hex_pub: str, receipt_events, listing_events,
                        now: float | None = None) -> dict:
    now = now or time.time()
    confirmed = [b for b in confirmed_receipts(receipt_events) if hex_pub in b["parties"]]
    partners = {p for b in confirmed for p in b["parties"] if p != hex_pub}
    kept = lapsed = 0
    for ev in latest_per_listing([e for e in listing_events if e.pubkey == hex_pub]):
        info = summarize_listing(ev)
        if info["status"] != "active" or info["is_live"]:
            kept += 1
        else:
            lapsed += 1
    stamps = [e.created_at for e in list(receipt_events) + list(listing_events)
              if e.pubkey == hex_pub]
    return {
        "pubkey": hex_pub,
        "confirmed_trades": len(confirmed),
        "trade_partners": len(partners),
        "listings_kept_up": kept,
        "listings_lapsed": lapsed,
        "first_seen": min(stamps) if stamps else None,
        "days_active": int((now - min(stamps)) / 86400) if stamps else 0,
    }


async def acompute(relays: list[str], who: str, days: float = WINDOW_DAYS) -> dict:
    hex_pub = _hex_pub(who)
    since = int(time.time() - days * 86400)
    receipts = await afetch_for(relays, hex_pub)
    listings = await afetch_events(relays, {"kinds": [KIND_CLASSIFIED], "authors": [hex_pub],
                                            "since": since, "limit": 1000}, 1000)
    return compute_from_events(hex_pub, receipts, listings)


def summary_line(r: dict) -> str:
    if not r["confirmed_trades"] and not (r["listings_kept_up"] + r["listings_lapsed"]):
        return "new here — no history seen yet"
    parts = [f"{r['confirmed_trades']} confirmed trade{'s' if r['confirmed_trades'] != 1 else ''}"]
    if r["trade_partners"]:
        parts.append(f"with {r['trade_partners']} {'people' if r['trade_partners'] != 1 else 'person'}")
    total = r["listings_kept_up"] + r["listings_lapsed"]
    if total:
        parts.append(f"{r['listings_kept_up']}/{total} listings kept up")
    if r["days_active"] >= 1:
        parts.append(f"active {r['days_active']}d")
    return " · ".join(parts)


def main(argv: list[str]) -> int:
    if not argv:
        console.print("Usage: pergamon reputation <npub-or-hex>")
        return 2
    try:
        r = asyncio.run(acompute(_load_relays_from_config(), argv[0]))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    console.print(f"[bold]{argv[0][:20]}…[/bold]  {summary_line(r)}")
    console.print("[dim]Counted from what this pergamon can see. Sync with neighbors for a "
                  "fuller picture.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
