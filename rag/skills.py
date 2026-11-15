"""Skills + services: advertise what you can do, not just what you have.

Kind 30407 addressable events, one per service you offer. Same freshness
model as classifieds (expires_at, renew, close) because a stale "I fix small
engines" listing is as useless as a stale "eggs for sale" one. Cert is a
self-claim; the social layer verifies it over time.

CLI:
    pergamon skills post --title "Small engine repair" --category repair \\
        --level journeyman --availability weekends --radius 15 \\
        --accepts barter,silver "Mowers, chainsaws, generators."
    pergamon skills list [--category medical]
    pergamon skills find "midwife"
    pergamon skills mine | renew <id> | close <id>
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time

from rich.console import Console
from rich.panel import Panel

from rag.classifieds import is_live, latest_per_listing
from rag.config import REPO_ROOT
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

KIND_SKILL = 30407
SKILL_TAG = "pergamon-skill"
DEFAULT_TTL_DAYS = 60  # services change slower than goods
CATEGORIES = ("medical", "repair", "building", "food", "farming", "animals", "teaching",
              "childcare", "transport", "comms", "sewing", "metalwork", "woodwork",
              "electrical", "labor", "other")
LEVELS = ("learning", "apprentice", "journeyman", "master")
MINE_PATH = REPO_ROOT / "build" / "skills" / "mine.json"


def build_skill_event(pubkey_hex: str, title: str, description: str, *,
                      category: str = "other", level: str | None = None,
                      availability: str | None = None, radius_mi: float | None = None,
                      location: str | None = None, accepts: list[str] | None = None,
                      rate: str | None = None, cert: str | None = None,
                      ttl_days: float = DEFAULT_TTL_DAYS, status: str = "active",
                      listing_id: str | None = None) -> Event:
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
    if level and level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    if not title.strip():
        raise ValueError("title required")
    tags = [
        ["d", listing_id or secrets.token_hex(8)],
        ["title", title.strip()],
        ["t", SKILL_TAG],
        ["t", f"{SKILL_TAG}-{category}"],
        ["category", category],
        ["status", status],
        ["published_at", str(int(time.time()))],
        ["expires_at", str(int(time.time() + ttl_days * 86400))],
    ]
    for key, val in (("level", level), ("availability", availability),
                     ("location", location), ("rate", rate), ("cert", cert)):
        if val:
            tags.append([key, str(val)])
    if radius_mi is not None:
        tags.append(["radius_mi", str(radius_mi)])
    if accepts:
        tags.append(["accepts", ",".join(accepts)])
    return build_event(kind=KIND_SKILL, content=description.strip(), pubkey_hex=pubkey_hex,
                       tags=tags)


def summarize(ev: Event) -> dict:
    tags = {t[0]: t[1] for t in ev.tags if len(t) > 1}
    info = {
        "id": ev.id, "pubkey": ev.pubkey, "created_at": ev.created_at,
        "d": tags.get("d", ""), "title": tags.get("title", ""),
        "description": ev.content, "category": tags.get("category", "other"),
        "level": tags.get("level", ""), "availability": tags.get("availability", ""),
        "location": tags.get("location", ""), "rate": tags.get("rate", ""),
        "cert": tags.get("cert", ""), "status": tags.get("status", "active"),
        "accepts": [a for a in tags.get("accepts", "").split(",") if a],
        "radius_mi": float(tags["radius_mi"]) if tags.get("radius_mi") else None,
        "expires_at": int(tags["expires_at"]) if tags.get("expires_at", "").isdigit() else None,
    }
    info["is_live"] = is_live(info)
    return info


def search(events: list[Event], query: str = "", category: str | None = None,
           live_only: bool = True) -> list[dict]:
    q = query.lower().strip()
    out = []
    for ev in latest_per_listing(events):
        info = summarize(ev)
        if live_only and not info["is_live"]:
            continue
        if category and info["category"] != category:
            continue
        hay = " ".join([info["title"], info["description"], info["category"],
                        info["cert"], info["location"]]).lower()
        if q and q not in hay:
            continue
        out.append(info)
    return out


async def afetch(relays: list[str], category: str | None = None) -> list[Event]:
    tag = f"{SKILL_TAG}-{category}" if category else SKILL_TAG
    return await afetch_events(relays, {"kinds": [KIND_SKILL], "#t": [tag], "limit": 500}, 500)


# ───────────────────────── local copy of mine ─────────────────────────

def _load_mine() -> dict:
    return json.loads(MINE_PATH.read_text()) if MINE_PATH.exists() else {}


def _remember(ev: Event) -> None:
    mine = _load_mine()
    mine[next(t[1] for t in ev.tags if t[0] == "d")] = ev.to_json()
    MINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MINE_PATH.write_text(json.dumps(mine, indent=2))


def resolve_ref(ref: str, pubkey_hex: str | None = None) -> str:
    """Full listing id from a full id or a unique prefix (the 6-character
    ref printed on the bulletin). Optionally limited to one author."""
    mine = _load_mine()
    ref = ref.strip().lower()
    if ref in mine and (pubkey_hex is None or mine[ref]["pubkey"] == pubkey_hex):
        return ref
    hits = [d for d, raw in mine.items()
            if d.startswith(ref) and (pubkey_hex is None or raw["pubkey"] == pubkey_hex)]
    if not hits:
        raise KeyError(f"no local skill listing matching {ref!r}")
    if len(hits) > 1:
        raise KeyError(f"ref {ref!r} matches {len(hits)} skill listings — use more characters")
    return hits[0]


def build_republish(d: str, status: str | None = None) -> Event:
    mine = _load_mine()
    d = resolve_ref(d)
    raw = mine[d]
    now = int(time.time())
    tags = [t for t in raw["tags"] if t[0] not in ("expires_at", "published_at")
            and not (status and t[0] == "status")]
    tags += [["expires_at", str(now + DEFAULT_TTL_DAYS * 86400)], ["published_at", str(now)]]
    if status:
        tags.append(["status", status])
    ev = build_event(KIND_SKILL, raw["content"], raw["pubkey"], tags)
    ev.created_at = max(ev.created_at, raw["created_at"] + 1)  # must supersede the old version
    return ev


def republish(d: str, nsec: str, status: str | None = None) -> Event:
    ev = sign_event(build_republish(d, status), nsec)
    _remember(ev)
    return ev


def post(ident, nsec: str, **fields) -> tuple[Event, dict]:
    ev = sign_event(build_skill_event(ident.hex_pub, **fields), nsec)
    results = publish(ev, _load_relays_from_config())
    _remember(ev)
    return ev, results


# ────────────────────────────── CLI ──────────────────────────────────

def _print(i: dict) -> None:
    head = f"[bold]{i['title']}[/bold]  [dim]{i['category']}{' · ' + i['level'] if i['level'] else ''}[/dim]"
    lines = []
    if i["description"]:
        lines.append(i["description"])
    meta = [m for m in (i["availability"], i["rate"], i["location"],
                        f"travels {i['radius_mi']:.0f} mi" if i["radius_mi"] else "",
                        "accepts " + ", ".join(i["accepts"]) if i["accepts"] else "",
                        "cert: " + i["cert"] if i["cert"] else "") if m]
    if meta:
        lines.append("[dim]" + " · ".join(meta) + "[/dim]")
    lines.append(f"[dim]id {i['d']} · from {i['pubkey'][:12]}…[/dim]")
    console.print(Panel("\n".join(lines), title=head, title_align="left", border_style="cyan"))


def main(argv: list[str]) -> int:
    if not argv:
        console.print(__doc__.split("CLI:")[1])
        return 2
    cmd, rest = argv[0], argv[1:]
    flags = {"--title": "title", "--category": "category", "--level": "level",
             "--availability": "availability", "--radius": "radius_mi",
             "--location": "location", "--accepts": "accepts", "--rate": "rate",
             "--cert": "cert"}
    fields: dict = {}
    words: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] in flags and i + 1 < len(rest):
            fields[flags[rest[i]]] = rest[i + 1]; i += 2
        else:
            words.append(rest[i]); i += 1

    try:
        if cmd == "post":
            ident = get_active()
            if not ident:
                raise ValueError("no active identity — pergamon identity create <name>")
            if "radius_mi" in fields:
                fields["radius_mi"] = float(fields["radius_mi"])
            if "accepts" in fields:
                fields["accepts"] = [a.strip() for a in fields["accepts"].split(",") if a.strip()]
            ev, results = post(ident, load_nsec(ident.name), title=fields.pop("title", ""),
                               description=" ".join(words), **fields)
            for url, (ok, msg) in results.items():
                console.print(f"  [{'green]ok' if ok else 'red]fail'}[/] {url} {msg}")
            console.print(f"skill id: [cyan]{summarize(ev)['d']}[/cyan]")
            return 0
        if cmd in ("list", "find"):
            events = asyncio.run(afetch(_load_relays_from_config(), fields.get("category")))
            hits = search(events, " ".join(words), fields.get("category"))
            if not hits:
                console.print("[yellow]no matching skills listed[/yellow]")
            for h in hits:
                _print(h)
            return 0
        if cmd == "mine":
            ident = get_active()
            rows = [summarize(Event(**{k: r[k] for k in ("kind", "content", "pubkey", "tags",
                                                         "created_at", "id", "sig")}))
                    for r in _load_mine().values() if ident and r["pubkey"] == ident.hex_pub]
            for r in rows:
                _print(r)
            if not rows:
                console.print("[dim]no skills posted yet[/dim]")
            return 0
        if cmd in ("renew", "close"):
            ident = get_active()
            if not ident or not words:
                raise ValueError(f"usage: pergamon skills {cmd} <id>")
            ev = republish(words[0], load_nsec(ident.name), "closed" if cmd == "close" else None)
            publish(ev, _load_relays_from_config())
            console.print(f"[green]{cmd}d[/green] {words[0]}")
            return 0
    except (ValueError, KeyError) as e:
        console.print(f"[red]{e}[/red]")
        return 1
    console.print(__doc__.split("CLI:")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
