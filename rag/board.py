"""Community board — local news, notices, events, lost & found.

A town bulletin board and local forum that keeps working without the internet.
Posts are plain Nostr kind-1 notes tagged ["t", "pergamon-board"] plus a
category tag, so any standard Nostr client can read them too.

Deliberately simple: no voting, no moderation queue, no reputation
weighting. Every post is signed, so authorship is always visible; the
social layer does the rest, same as a corkboard at the general store.

CLI:
    pergamon board post --category news "Bridge on Rt 9 washed out"
    pergamon board list [--category events] [--days 30]
"""
from __future__ import annotations

import asyncio
import sys
import time

from rich.console import Console
from rich.panel import Panel

from rag.identity import get_active, load_nsec
from rag.nostr import (
    Event,
    _load_relays_from_config,
    afetch_events,
    build_event,
    publish,
    sign_event,
)

console = Console()

BOARD_TAG = "pergamon-board"
CATEGORIES = {
    "news": "Local news",
    "notice": "Notices",
    "event": "Events",
    "help": "Help wanted / offered",
    "lost-found": "Lost & found",
    "warning": "Warnings",
}
DEFAULT_DAYS = 30
MAX_LEN = 2000


def build_post(pubkey_hex: str, text: str, category: str,
               location: str | None = None) -> Event:
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
    text = text.strip()
    if not text:
        raise ValueError("post is empty")
    if len(text) > MAX_LEN:
        raise ValueError(f"post is longer than {MAX_LEN} characters")
    tags = [["t", BOARD_TAG], ["t", f"{BOARD_TAG}-{category}"], ["category", category]]
    if location:
        tags.append(["location", location])
    return build_event(kind=1, content=text, pubkey_hex=pubkey_hex, tags=tags)


def summarize(ev: Event) -> dict:
    category = next((t[1] for t in ev.tags if t[0] == "category" and len(t) > 1), "notice")
    location = next((t[1] for t in ev.tags if t[0] == "location" and len(t) > 1), "")
    return {
        "id": ev.id, "pubkey": ev.pubkey, "created_at": ev.created_at,
        "category": category, "category_title": CATEGORIES.get(category, category),
        "location": location, "text": ev.content,
    }


async def afetch_posts(relays: list[str], category: str | None = None,
                       days: float = DEFAULT_DAYS, limit: int = 200) -> list[dict]:
    tag = f"{BOARD_TAG}-{category}" if category else BOARD_TAG
    filters = {"kinds": [1], "#t": [tag], "since": int(time.time() - days * 86400),
               "limit": limit}
    return [summarize(ev) for ev in await afetch_events(relays, filters, limit)]


def fetch_posts(relays: list[str], category: str | None = None,
                days: float = DEFAULT_DAYS, limit: int = 200) -> list[dict]:
    return asyncio.run(afetch_posts(relays, category, days, limit))


def post(ident, nsec: str, text: str, category: str,
         location: str | None = None) -> tuple[Event, dict]:
    ev = sign_event(build_post(ident.hex_pub, text, category, location), nsec)
    return ev, publish(ev, _load_relays_from_config())


# ────────────────────────────── CLI ──────────────────────────────────

def _print(p: dict) -> None:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(p["created_at"]))
    title = f"[bold]{p['category_title']}[/bold]  [dim]{when} · {p['pubkey'][:12]}…[/dim]"
    body = p["text"] + (f"\n[dim]{p['location']}[/dim]" if p["location"] else "")
    console.print(Panel(body, title=title, border_style="cyan", title_align="left"))


def main(argv: list[str]) -> int:
    usage = (
        "Usage:\n"
        f"  pergamon board post --category {{{'|'.join(CATEGORIES)}}} [--location X] \"text\"\n"
        "  pergamon board list [--category X] [--days 30]"
    )
    if not argv:
        console.print(usage)
        return 2
    cmd, rest = argv[0], argv[1:]
    opts = {"category": None, "location": None, "days": DEFAULT_DAYS}
    words: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--category", "-c") and i + 1 < len(rest):
            opts["category"] = rest[i + 1]; i += 2
        elif a == "--location" and i + 1 < len(rest):
            opts["location"] = rest[i + 1]; i += 2
        elif a == "--days" and i + 1 < len(rest):
            opts["days"] = float(rest[i + 1]); i += 2
        else:
            words.append(a); i += 1

    if cmd == "post":
        ident = get_active()
        if not ident:
            console.print("[red]no active identity.[/red] pergamon identity create <name>")
            return 1
        try:
            ev, results = post(ident, load_nsec(ident.name), " ".join(words),
                               opts["category"] or "notice", opts["location"])
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return 2
        ok = False
        for url, (good, msg) in results.items():
            console.print(f"  [{'green]ok' if good else 'red]fail'}[/] {url} {msg}")
            ok = ok or good
        return 0 if ok else 1

    if cmd == "list":
        posts = fetch_posts(_load_relays_from_config(), opts["category"], opts["days"])
        if not posts:
            console.print("[yellow]no board posts in that window[/yellow]")
            return 0
        for p in posts:
            _print(p)
        return 0

    console.print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
