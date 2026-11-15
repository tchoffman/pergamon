"""Two-party trade receipts — the evidence reputation is built on.

A receipt is confirmed only when BOTH parties have published a kind-30405
event with the same d-tag and the same body, each tagging the other with
["p", ...]. One-sided receipts are just claims and don't count.

Flow:
    # Alice, after trading:
    pergamon receipt draft --with npub1bob... --gave "0.3 oz Ag" --got "20 lb apples"
    → prints a receipt code (and QR on the web UI); Alice's half is published.

    # Bob, on his own pergamon:
    pergamon receipt confirm <code>
    → checks he's a party, publishes his matching half.

    pergamon receipt list        # mine: confirmed / waiting on the other side
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import time
from datetime import date

from rich.console import Console
from rich.table import Table

from rag.config import REPO_ROOT
from rag.identity import _bech32_to_hex, get_active, load_nsec
from rag.nostr import (
    Event,
    _load_relays_from_config,
    afetch_events,
    build_event,
    publish,
    sign_event,
)

console = Console()

KIND_RECEIPT = 30405
RECEIPT_TAG = "pergamon-receipt"
MINE_PATH = REPO_ROOT / "build" / "receipts" / "mine.json"


def _hex_pub(who: str) -> str:
    who = who.strip()
    if who.startswith("npub1"):
        return _bech32_to_hex("npub", who)
    if len(who) == 64 and all(c in "0123456789abcdef" for c in who.lower()):
        return who.lower()
    raise ValueError(f"not an npub or hex pubkey: {who!r}")


def canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def body_hash(body: dict) -> str:
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def make_body(drafter_hex: str, counterparty_hex: str, gave: str, got: str,
              listing: str | None = None, note: str | None = None) -> dict:
    if drafter_hex == counterparty_hex:
        raise ValueError("a receipt needs two different parties")
    if not gave.strip() or not got.strip():
        raise ValueError("say what each side gave")
    body = {
        "id": secrets.token_hex(8),
        "parties": sorted([drafter_hex, counterparty_hex]),
        "from": drafter_hex,
        "gave": gave.strip(),
        "got": got.strip(),
        "date": date.today().isoformat(),
    }
    if listing:
        body["listing"] = listing
    if note:
        body["note"] = note.strip()
    return body


def encode_code(body: dict) -> str:
    return base64.urlsafe_b64encode(canonical(body).encode()).decode().rstrip("=")


def decode_code(code: str) -> dict:
    code = code.strip()
    try:
        raw = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
        body = json.loads(raw)
    except ValueError as e:
        raise ValueError("that receipt code is damaged or incomplete") from e
    for key in ("id", "parties", "from", "gave", "got", "date"):
        if key not in body:
            raise ValueError(f"receipt code missing {key!r}")
    if len(body["parties"]) != 2:
        raise ValueError("receipt must have exactly two parties")
    return body


def build_receipt_event(signer_hex: str, body: dict) -> Event:
    if signer_hex not in body["parties"]:
        raise ValueError("you are not a party to this receipt")
    other = next(p for p in body["parties"] if p != signer_hex)
    tags = [["d", body["id"]], ["p", other], ["t", RECEIPT_TAG], ["body", body_hash(body)]]
    if body.get("listing"):
        tags.append(["listing", body["listing"]])
    return build_event(KIND_RECEIPT, canonical(body), signer_hex, tags)


def confirmed_receipts(events: list[Event]) -> list[dict]:
    """Bodies for which both parties published a matching half."""
    halves: dict[tuple[str, str], set[str]] = {}
    bodies: dict[tuple[str, str], dict] = {}
    for ev in events:
        if ev.kind != KIND_RECEIPT:
            continue
        try:
            body = json.loads(ev.content)
        except ValueError:
            continue
        h = next((t[1] for t in ev.tags if t[0] == "body" and len(t) > 1), None)
        p = next((t[1] for t in ev.tags if t[0] == "p" and len(t) > 1), None)
        parties = body.get("parties") or []
        if h != body_hash(body) or ev.pubkey not in parties or p not in parties or p == ev.pubkey:
            continue
        key = (body.get("id", ""), h)
        halves.setdefault(key, set()).add(ev.pubkey)
        bodies[key] = body
    return [bodies[k] for k, signers in halves.items()
            if signers == set(bodies[k]["parties"])]


async def afetch_for(relays: list[str], hex_pub: str) -> list[Event]:
    """Every receipt half involving hex_pub (authored by them or tagging them)."""
    by_me = await afetch_events(relays, {"kinds": [KIND_RECEIPT], "authors": [hex_pub],
                                         "limit": 1000}, 1000)
    to_me = await afetch_events(relays, {"kinds": [KIND_RECEIPT], "#p": [hex_pub],
                                         "limit": 1000}, 1000)
    seen = {e.id: e for e in by_me + to_me}
    return list(seen.values())


# ───────────────────────── local bookkeeping ──────────────────────────

def _load_mine() -> dict:
    return json.loads(MINE_PATH.read_text()) if MINE_PATH.exists() else {}


def _remember(body: dict) -> None:
    mine = _load_mine()
    mine[body["id"]] = body
    MINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MINE_PATH.write_text(json.dumps(mine, indent=2))


def draft(ident, nsec: str, counterparty: str, gave: str, got: str,
          listing: str | None = None, note: str | None = None) -> tuple[dict, str, dict]:
    body = make_body(ident.hex_pub, _hex_pub(counterparty), gave, got, listing, note)
    ev = sign_event(build_receipt_event(ident.hex_pub, body), nsec)
    results = publish(ev, _load_relays_from_config())
    _remember(body)
    return body, encode_code(body), results


def confirm(ident, nsec: str, code: str) -> tuple[dict, dict]:
    body = decode_code(code)
    ev = sign_event(build_receipt_event(ident.hex_pub, body), nsec)
    results = publish(ev, _load_relays_from_config())
    _remember(body)
    return body, results


def describe(body: dict, me_hex: str) -> str:
    """Human sentence from my point of view."""
    if body["from"] == me_hex:
        return f"I gave {body['gave']} and got {body['got']}"
    return f"I gave {body['got']} and got {body['gave']}"


# ────────────────────────────── CLI ──────────────────────────────────

def main(argv: list[str]) -> int:
    usage = __doc__.split("Flow:")[1]
    if not argv:
        console.print(usage)
        return 2
    cmd, rest = argv[0], argv[1:]
    opts: dict = {}
    pos: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] in ("--with", "--gave", "--got", "--listing", "--note") and i + 1 < len(rest):
            opts[rest[i][2:]] = rest[i + 1]; i += 2
        else:
            pos.append(rest[i]); i += 1
    ident = get_active()
    if not ident:
        console.print("[red]no active identity — pergamon identity create <name>[/red]")
        return 1
    try:
        if cmd == "draft":
            for k in ("with", "gave", "got"):
                if k not in opts:
                    raise ValueError(f"--{k} is required")
            body, code, results = draft(ident, load_nsec(ident.name), opts["with"],
                                        opts["gave"], opts["got"], opts.get("listing"),
                                        opts.get("note"))
            for url, (ok, msg) in results.items():
                console.print(f"  [{'green]ok' if ok else 'red]fail'}[/] {url} {msg}")
            console.print("\nGive this code to the other party to confirm:\n")
            console.print(f"[bold]{code}[/bold]\n")
            console.print("[dim]They run: pergamon receipt confirm <code>[/dim]")
            return 0
        if cmd == "confirm":
            if not pos:
                raise ValueError("usage: pergamon receipt confirm <code>")
            body, results = confirm(ident, load_nsec(ident.name), pos[0])
            for url, (ok, msg) in results.items():
                console.print(f"  [{'green]ok' if ok else 'red]fail'}[/] {url} {msg}")
            console.print(f"[green]confirmed:[/green] {describe(body, ident.hex_pub)}")
            return 0
        if cmd == "list":
            relays = _load_relays_from_config()
            confirmed_ids = {b["id"] for b in confirmed_receipts(
                asyncio.run(afetch_for(relays, ident.hex_pub)))}
            mine = _load_mine()
            if not mine:
                console.print("[dim]no receipts yet[/dim]")
                return 0
            t = Table(title="My trade receipts")
            for c in ("date", "with", "what happened", "status"):
                t.add_column(c)
            for b in sorted(mine.values(), key=lambda b: b["date"], reverse=True):
                other = next(p for p in b["parties"] if p != ident.hex_pub)
                status = ("[green]confirmed[/green]" if b["id"] in confirmed_ids
                          else "[yellow]waiting on other party[/yellow]")
                t.add_row(b["date"], other[:12] + "…", describe(b, ident.hex_pub), status)
            console.print(t)
            return 0
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    console.print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
