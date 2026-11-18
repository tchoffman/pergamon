"""Feeds: neighborhood newscasts people subscribe to.

Each feed is a board category (plus the morning weather). Subscribers get a
short digest at set times with only what's new since the last one; warnings
go out within minutes. Radio subscribers are radios (!node ids) and get radio
messages; web subscribers are account keys and get a sealed message from the
hub in their Messages app. Hidden and blocked posts never appear, because
posts are read through the moderation filter like everywhere else.

    # config/install.local.yaml (optional)
    feeds:
      times: ["07:00", "12:00", "18:00"]

    pergamon feeds                    # list feeds and subscriber counts
    pergamon feeds preview [feed]     # what the next digest would say
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path

from rag.config import REPO_ROOT

STATE = REPO_ROOT / "build" / "state"
SUBS_PATH = STATE / "feeds.json"
SENT_PATH = STATE / "feeds_sent.json"
DEFAULT_TIMES = ["07:00", "12:00", "18:00"]
URGENT = {"warnings"}
DIGEST_BYTES = 400      # two radio messages
ITEM_CHARS = 110
LATE_LIMIT = dt.timedelta(hours=2)
_lock = threading.Lock()

# feed id → (title, board category or None for weather)
FEEDS = {
    "weather": ("Morning weather", None),
    "news": ("Local news", "news"),
    "notices": ("Notices and announcements", "notice"),
    "events": ("Events", "event"),
    "help": ("Help wanted / offered", "help"),
    "lost-found": ("Lost & found", "lost-found"),
    "warnings": ("Warnings (sent right away)", "warning"),
}


class FeedError(ValueError):
    pass


# ───────────────────────────── subscriptions ─────────────────────────────

def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def feed_id(word: str) -> str:
    w = (word or "").strip().lower().rstrip("s")
    for fid in FEEDS:
        if w in (fid, fid.rstrip("s"), FEEDS[fid][1] or ""):
            return fid
    if w in ("lost", "found", "lost&found"):
        return "lost-found"
    if w in ("announcement", "notice"):
        return "notices"
    raise FeedError(f"No feed called '{word}'. Feeds: {', '.join(FEEDS)}")


def subscriptions(who: str) -> list[str]:
    return list(_read(SUBS_PATH).get(who, []))


def set_subscriptions(who: str, feeds: list[str]) -> list[str]:
    feeds = sorted({feed_id(f) for f in feeds})
    with _lock:
        data = _read(SUBS_PATH)
        if feeds:
            data[who] = feeds
        else:
            data.pop(who, None)
        _write(SUBS_PATH, data)
    return feeds


def subscribe(who: str, feed: str) -> list[str]:
    return set_subscriptions(who, subscriptions(who) + [feed_id(feed)])


def unsubscribe(who: str, feed: str) -> list[str]:
    fid = "all" if feed.strip().lower() == "all" else feed_id(feed)
    return set_subscriptions(who, [] if fid == "all" else [f for f in subscriptions(who) if f != fid])


def subscribers(feed: str) -> list[str]:
    return sorted(w for w, fs in _read(SUBS_PATH).items() if feed in fs)


def is_radio(who: str) -> bool:
    return who.startswith("!")


# ───────────────────────────── digests ─────────────────────────────

def _shorten(text: str, n: int = ITEM_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def compose(feed: str, posts: list[dict]) -> str:
    """'News: item; item; item (+2 more on the board)', at most DIGEST_BYTES."""
    title = FEEDS[feed][0].split(" (")[0]
    head = f"{title}: "
    parts: list[str] = []
    for i, p in enumerate(posts):
        item = _shorten(p["text"]) + (f" ({p['location']})" if p.get("location") else "")
        more = len(posts) - i - 1
        tail = f" (+{more} more on the board)" if more else ""
        if len((head + "; ".join(parts + [item]) + tail).encode()) > DIGEST_BYTES:
            rest = len(posts) - i
            return head + "; ".join(parts) + f" (+{rest} more on the board)"
        parts.append(item)
    return head + "; ".join(parts)


def new_posts(feed: str, since: float, relays: list[str] | None = None) -> list[dict]:
    from rag import board
    from rag.nostr import _load_relays_from_config
    category = FEEDS[feed][1]
    if not category:
        return []
    days = max(1.0, (time.time() - since) / 86400 + 0.1)
    posts = board.fetch_posts(relays or _load_relays_from_config(), category, days=min(days, 30))
    return sorted((p for p in posts if p["created_at"] > since), key=lambda p: p["created_at"])


def times() -> list[dt.time]:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    out = []
    for t in (local.get("feeds") or {}).get("times") or DEFAULT_TIMES:
        hh, mm = (int(x) for x in str(t).split(":"))
        out.append(dt.time(hh, mm))
    return sorted(out)


def due_slot(now: dt.datetime, sent: dict) -> str | None:
    """The digest time that's due now and not yet sent, like '2026-11-18 12:00'."""
    for t in reversed(times()):
        at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if at <= now < at + LATE_LIMIT:
            slot = at.strftime("%Y-%m-%d %H:%M")
            return None if sent.get("slot") == slot else slot
    return None


def plan(now: dt.datetime | None = None, relays: list[str] | None = None) -> list[tuple[str, str]]:
    """(subscriber, text) pairs to send right now: a scheduled digest if one
    is due, and any urgent posts since the last check. Records what it plans,
    so each post goes out once."""
    now = now or dt.datetime.now().astimezone()
    out: list[tuple[str, str]] = []
    with _lock:
        sent = _read(SENT_PATH)
        last = sent.get("last", {})
        start = sent.get("started") or time.time()
        sent.setdefault("started", start)
        slot = due_slot(now, sent)
        feeds = [f for f in FEEDS if FEEDS[f][1] and (f in URGENT or slot)]
        for feed in feeds:
            subs = subscribers(feed)
            since = float(last.get(feed, start))
            if not subs:
                last[feed] = time.time()
                continue
            posts = new_posts(feed, since, relays)
            if posts:
                text = compose(feed, posts)
                out += [(s, text) for s in subs]
                last[feed] = max(p["created_at"] for p in posts)
        if slot:
            sent["slot"] = slot
        sent["last"] = last
        _write(SENT_PATH, sent)
    return out


def weather_subscribers() -> list[str]:
    """Radios subscribed to the weather feed (they get the morning report)."""
    return [w for w in subscribers("weather") if is_radio(w)]


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        print("Digest times:", ", ".join(t.strftime("%H:%M") for t in times()))
        for fid, (title, _) in FEEDS.items():
            print(f"  {fid:<11} {title:<28} {len(subscribers(fid))} subscriber(s)")
        return 0
    if cmd == "preview":
        feeds = [feed_id(argv[1])] if len(argv) > 1 else [f for f in FEEDS if FEEDS[f][1]]
        day = time.time() - 86400
        for f in feeds:
            posts = new_posts(f, day)
            print(compose(f, posts) if posts else f"{FEEDS[f][0]}: nothing new in the last day")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
