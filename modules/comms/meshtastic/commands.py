"""Radio commands: reach the community board, classifieds and skills over LoRa.

Someone with only a handheld radio has no browser, so the bulletin board and
marketplace are invisible to them. These commands answer from the same Nostr
relays the web pages read, in a few short lines.

Commands start with "/" so a question like "find water in the desert?" still
goes to the library. Bare "ping" and "help" also work.

    /news [category]   latest board posts
    /find <words>      live classifieds matching the words
    /skills <words>    people offering a matching skill
    /msg <who> <text>  leave a message for another radio (handled by the listener)
"""
from __future__ import annotations

import time
from typing import Callable

HELP = ("Pergamon offline library. DM any question for an answer. Commands: "
        "/news, /find <item>, /skills <skill>, /msg <name> <text>, ping.")
MAX_ITEMS = 3
SNIPPET = 90


def _relays() -> list[str]:
    from rag.nostr import _load_relays_from_config
    return _load_relays_from_config()


def _ago(ts: int, now: float | None = None) -> str:
    s = max(0, int((now or time.time()) - ts))
    if s < 3600:
        return f"{max(1, s // 60)}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _clip(text: str, n: int = SNIPPET) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _names(relays: list[str], pubkeys) -> dict[str, str]:
    try:
        from rag.profiles import fetch_names
        return fetch_names(relays, set(pubkeys))
    except Exception:
        return {}


def _who(names: dict[str, str], pubkey: str) -> str:
    return names.get(pubkey) or pubkey[:8]


def news(arg: str, relays: list[str]) -> str:
    from rag import board
    category = arg.strip().lower() or None
    if category and category not in board.CATEGORIES:
        return f"No board category '{category}'. Try: {', '.join(board.CATEGORIES)}."
    posts = sorted(board.fetch_posts(relays, category), key=lambda p: p["created_at"], reverse=True)
    if not posts:
        return "Board is empty" + (f" for {category}." if category else ".")
    top = posts[:MAX_ITEMS]
    names = _names(relays, (p["pubkey"] for p in top))
    lines = [f"{p['category_title']} {_ago(p['created_at'])} ago, {_who(names, p['pubkey'])}: "
             f"{_clip(p['text'])}" for p in top]
    more = f" (+{len(posts) - MAX_ITEMS} more)" if len(posts) > MAX_ITEMS else ""
    return "\n".join(lines) + more


def _price(info: dict) -> str:
    if info.get("price_oz_ag") is not None:
        return f"{info['price_oz_ag']:g} oz silver"
    if info.get("price_usd") is not None:
        return f"${info['price_usd']:g}"
    if info.get("accepts"):
        return "trade: " + ", ".join(info["accepts"][:2])
    return ""


def find(arg: str, relays: list[str]) -> str:
    from rag import classifieds
    q = arg.strip()
    if not q:
        return "Usage: /find <item>, e.g. /find eggs"
    infos = classifieds.search_infos(classifieds.fetch_listings(relays), q)
    if not infos:
        return f"No live listings for '{q}'."
    infos.sort(key=lambda i: i["created_at"], reverse=True)
    lines = []
    for i in infos[:MAX_ITEMS]:
        bits = [("WANTED " if i["side"] == "seeking" else "") + i["title"], _price(i), i["location"]]
        lines.append(" | ".join(b for b in bits if b) + f" [{i['d'][:6]}]")
    more = f" (+{len(infos) - MAX_ITEMS} more)" if len(infos) > MAX_ITEMS else ""
    return "\n".join(lines) + more + "\nDetails at the Pergamon bulletin."


def skills(arg: str, relays: list[str]) -> str:
    import asyncio
    from rag import skills as sk
    q = arg.strip()
    if not q:
        return "Usage: /skills <skill>, e.g. /skills welding"
    infos = sk.search(asyncio.run(sk.afetch(relays)), q)
    if not infos:
        return f"No one listed for '{q}'."
    infos.sort(key=lambda i: i["created_at"], reverse=True)
    names = _names(relays, (i["pubkey"] for i in infos[:MAX_ITEMS]))
    lines = [" | ".join(b for b in [f"{_who(names, i['pubkey'])}: {i['title']}", i["level"],
                                     i["availability"], i["location"]] if b)
             for i in infos[:MAX_ITEMS]]
    more = f" (+{len(infos) - MAX_ITEMS} more)" if len(infos) > MAX_ITEMS else ""
    return "\n".join(lines) + more


def _msg_needs_radio(arg: str, relays: list[str]) -> str:
    return "Radio mail is handled by 'pergamon mesh listen' on a real radio."


COMMANDS: dict[str, Callable[[str, list[str]], str]] = {
    "msg": _msg_needs_radio,
    "news": news, "board": news, "find": find, "skills": skills, "skill": skills,
}


def run(text: str, relays: list[str] | None = None) -> str | None:
    """Reply text if `text` is a command, else None (it's a question)."""
    t = text.strip()
    bare = t.lower().rstrip("?!.")
    if bare == "ping":
        return "pong"
    if bare in ("help", "?", "", "/help"):
        return HELP
    if not t.startswith("/"):
        return None
    name, _, arg = t[1:].partition(" ")
    fn = COMMANDS.get(name.lower())
    if fn is None:
        return f"Unknown command /{name}. " + HELP
    relays = relays if relays is not None else _relays()
    live = [r for r in relays if _reachable(r)]
    if not live:
        # Otherwise every command would claim "nothing found", which is a lie.
        return OFFLINE
    return fn(arg, live)


OFFLINE = "The community board is offline right now (relay not reachable). Questions still work."


def _reachable(url: str, timeout: float = 2.0) -> bool:
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    port = u.port or (443 if u.scheme == "wss" else 80)
    try:
        with socket.create_connection((u.hostname or "localhost", port), timeout=timeout):
            return True
    except OSError:
        return False
