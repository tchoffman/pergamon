"""Library + lending: track what you own, who has it, and let neighbors find it.

Backed by two ledgers (config/ledgers/library.yaml, loans.yaml). Lendable or
seedable items can be published as one Nostr catalog event (kind 30406,
addressable, d="library-catalog") that other pergamons can search. Only
title, type, and creator leave the machine — never location or serial.

CLI:
    pergamon library add title="Dune" type=book creator="Frank Herbert" lendable=yes
    pergamon library list [--type tool] [--lendable]
    pergamon library lend <item_id> --to <npub-or-name> [--due 14d] [--borrowed]
    pergamon library return <loan_id> [--condition "..."]
    pergamon library loans [--open] [--overdue]
    pergamon library publish            # share your catalog (refresh monthly)
    pergamon library find "chainsaw" [--type tool]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import date, timedelta

from rich.console import Console
from rich.table import Table

from rag import ledger
from rag.classifieds import latest_per_listing
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

KIND_CATALOG = 30406
CATALOG_D = "library-catalog"
CATALOG_TAG = "pergamon-library"
CATALOG_TTL_DAYS = 45
PUBLIC_FIELDS = ("title", "type", "creator")


def _due(spec: str | None) -> str | None:
    if not spec:
        return None
    spec = spec.strip()
    if spec.endswith("d") and spec[:-1].isdigit():
        return (date.today() + timedelta(days=int(spec[:-1]))).isoformat()
    return date.fromisoformat(spec).isoformat()


def lend(item_id: int, counterparty: str, due: str | None = None,
         direction: str = "lent_out", notes: str | None = None) -> int:
    item = ledger.get_row("library", item_id)
    if not item:
        raise KeyError(f"no library item {item_id}")
    if direction == "lent_out" and open_loan_for(item_id):
        raise ValueError(f"{item['title']!r} is already out on loan")
    values = {"item_id": str(item_id), "title": item["title"], "counterparty": counterparty,
              "direction": direction, "loaned_at": date.today().isoformat()}
    if due:
        values["due_at"] = _due(due)
    if notes:
        values["notes"] = notes
    return ledger.add_row("loans", values)


def return_loan(loan_id: int, condition_change: str | None = None) -> bool:
    values = {"returned_at": date.today().isoformat()}
    if condition_change:
        values["condition_change"] = condition_change
    return ledger.update_row("loans", loan_id, values)


def open_loans() -> list[dict]:
    return [r for r in ledger.query_rows("loans") if not r.get("returned_at")]


def open_loan_for(item_id: int) -> dict | None:
    for r in open_loans():
        if r.get("item_id") == item_id and r.get("direction") == "lent_out":
            return r
    return None


def overdue(today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [r for r in open_loans() if r.get("due_at") and date.fromisoformat(r["due_at"]) < today]


def catalog_items() -> list[dict]:
    """Public view of lendable/seedable items, minus anything currently lent."""
    out_ids = {r["item_id"] for r in open_loans() if r.get("direction") == "lent_out"}
    items = []
    for r in ledger.query_rows("library"):
        if not (r.get("lendable") or r.get("seedable")):
            continue
        entry = {k: r[k] for k in PUBLIC_FIELDS if r.get(k)}
        entry["available"] = r["id"] not in out_ids
        if r.get("seedable"):
            entry["seedable"] = True
        items.append(entry)
    return items


def build_catalog_event(pubkey_hex: str, items: list[dict]) -> Event:
    tags = [
        ["d", CATALOG_D],
        ["t", CATALOG_TAG],
        ["expires_at", str(int(time.time() + CATALOG_TTL_DAYS * 86400))],
        ["count", str(len(items))],
    ]
    for kind in sorted({i.get("type", "other") for i in items}):
        tags.append(["t", f"{CATALOG_TAG}-{kind}"])
    return build_event(kind=KIND_CATALOG, content=json.dumps(items, separators=(",", ":")),
                       pubkey_hex=pubkey_hex, tags=tags)


def parse_catalog(ev: Event) -> list[dict]:
    try:
        items = json.loads(ev.content)
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict) and i.get("title")]


def _catalog_live(ev: Event, now: float | None = None) -> bool:
    exp = next((t[1] for t in ev.tags if t[0] == "expires_at" and len(t) > 1), None)
    try:
        return (now or time.time()) < int(exp)
    except (TypeError, ValueError):
        return False


def search_catalogs(events: list[Event], query: str = "", kind: str | None = None) -> list[dict]:
    q = query.lower().strip()
    hits = []
    for ev in latest_per_listing(events):
        if not _catalog_live(ev):
            continue
        for item in parse_catalog(ev):
            if kind and item.get("type") != kind:
                continue
            hay = f"{item.get('title', '')} {item.get('creator', '')}".lower()
            if q and q not in hay:
                continue
            hits.append({**item, "owner": ev.pubkey, "catalog_at": ev.created_at})
    return hits


async def afind(relays: list[str], query: str = "", kind: str | None = None) -> list[dict]:
    events = await afetch_events(relays, {"kinds": [KIND_CATALOG], "#t": [CATALOG_TAG],
                                          "limit": 500}, 500)
    return search_catalogs(events, query, kind)


# ────────────────────────────── CLI ──────────────────────────────────

def _flags(argv: list[str], names: dict[str, bool]) -> tuple[dict, list[str]]:
    """names maps flag → takes_value."""
    opts: dict = {}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in names:
            if names[a]:
                if i + 1 >= len(argv):
                    raise ValueError(f"{a} needs a value")
                opts[a] = argv[i + 1]; i += 2
            else:
                opts[a] = True; i += 1
        else:
            rest.append(a); i += 1
    return opts, rest


def _loans_table(rows: list[dict], title: str) -> None:
    t = Table(title=title)
    for c in ("loan", "item", "title", "direction", "with", "since", "due"):
        t.add_column(c)
    today = date.today()
    for r in rows:
        due = r.get("due_at") or ""
        if due and date.fromisoformat(due) < today and not r.get("returned_at"):
            due = f"[red]{due} (overdue)[/red]"
        t.add_row(str(r["id"]), str(r.get("item_id") or ""), r["title"], r["direction"],
                  r["counterparty"][:20], r["loaned_at"], due)
    console.print(t)


USAGE = __doc__.split("CLI:")[1]


def main(argv: list[str]) -> int:
    if not argv:
        console.print("Usage:" + USAGE)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "add":
            rid = ledger.add_row("library", ledger._parse_kv(rest))
            console.print(f"[green]added[/green] library#{rid}")
            return 0

        if cmd == "list":
            opts, _ = _flags(rest, {"--type": True, "--lendable": False})
            filters = []
            if "--type" in opts:
                filters.append(f"type={opts['--type']}")
            if opts.get("--lendable"):
                filters.append("lendable=1")
            ledger.show("library", filters)
            return 0

        if cmd == "lend":
            opts, pos = _flags(rest, {"--to": True, "--due": True, "--borrowed": False,
                                      "--notes": True})
            if not pos or "--to" not in opts:
                raise ValueError("usage: pergamon library lend <item_id> --to <who> [--due 14d]")
            lid = lend(int(pos[0]), opts["--to"], opts.get("--due"),
                       "borrowed" if opts.get("--borrowed") else "lent_out", opts.get("--notes"))
            console.print(f"[green]recorded loan #{lid}[/green]")
            return 0

        if cmd == "return":
            opts, pos = _flags(rest, {"--condition": True})
            if not pos:
                raise ValueError("usage: pergamon library return <loan_id> [--condition ...]")
            ok = return_loan(int(pos[0]), opts.get("--condition"))
            console.print("[green]returned[/green]" if ok else "[yellow]no such loan[/yellow]")
            return 0 if ok else 1

        if cmd == "loans":
            opts, _ = _flags(rest, {"--open": False, "--overdue": False})
            if opts.get("--overdue"):
                rows, title = overdue(), "Overdue loans"
            elif opts.get("--open"):
                rows, title = open_loans(), "Open loans"
            else:
                rows, title = ledger.query_rows("loans"), "All loans"
            if not rows:
                console.print(f"[dim]{title}: none[/dim]")
                return 0
            _loans_table(rows, title)
            return 0

        if cmd == "publish":
            ident = get_active()
            if not ident:
                raise ValueError("no active identity — pergamon identity create <name>")
            items = catalog_items()
            ev = sign_event(build_catalog_event(ident.hex_pub, items), load_nsec(ident.name))
            results = publish(ev, _load_relays_from_config())
            for url, (ok, msg) in results.items():
                console.print(f"  [{'green]ok' if ok else 'red]fail'}[/] {url} {msg}")
            console.print(f"published {len(items)} item(s); expires in {CATALOG_TTL_DAYS} days")
            return 0 if any(ok for ok, _ in results.values()) else 1

        if cmd == "find":
            opts, pos = _flags(rest, {"--type": True})
            hits = asyncio.run(afind(_load_relays_from_config(), " ".join(pos), opts.get("--type")))
            if not hits:
                console.print("[yellow]nobody's catalog has that[/yellow]")
                return 0
            t = Table(title="Found in neighbors' libraries")
            for c in ("title", "type", "creator", "available", "owner"):
                t.add_column(c)
            for h in hits:
                t.add_row(h["title"], h.get("type", ""), h.get("creator", ""),
                          "yes" if h.get("available", True) else "[dim]on loan[/dim]",
                          h["owner"][:16] + "…")
            console.print(t)
            return 0
    except (ValueError, KeyError) as e:
        console.print(f"[red]{e}[/red]")
        return 1

    console.print("Usage:" + USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
