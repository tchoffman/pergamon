"""The host's moderation list: hidden posts and blocked keys.

Each host moderates their own hub. Hidden and blocked content is dropped
wherever this hub reads events (rag/nostr.py), so it disappears from every
page, radio command and printed bulletin, and sync never passes it on. Other
hubs are untouched: nothing is deleted anywhere.

Listings and skills are "addressable" events: editing one publishes a new
event with a new id. So those are hidden by address (kind:author:d) as well
as by id, or an edit would bring them back.

    pergamon moderation                      # list
    pergamon moderation hide <event id>
    pergamon moderation block <npub or hex> [note]
    pergamon moderation unhide <event id> | unblock <npub or hex>
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from rag.config import REPO_ROOT

PATH = REPO_ROOT / "build" / "state" / "moderation.json"
_lock = threading.Lock()
_cache: dict = {"mtime": None, "path": None, "data": None}


def _empty() -> dict:
    return {"hidden": {}, "blocked": {}}


def load(path: Path | None = None) -> dict:
    """The list, re-read only when the file changes (it's checked per event)."""
    path = path or PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _empty()
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["data"]
    try:
        data = json.loads(path.read_text())
        data = {"hidden": dict(data.get("hidden") or {}), "blocked": dict(data.get("blocked") or {})}
    except (OSError, ValueError):
        data = _empty()
    _cache.update(mtime=mtime, path=path, data=data)
    return data


def _save(data: dict, path: Path | None = None) -> None:
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def address(kind: int, pubkey: str, tags: list) -> str | None:
    """kind:author:d for addressable events (30000-39999), else None."""
    if not 30000 <= int(kind) < 40000:
        return None
    d = next((t[1] for t in tags if len(t) > 1 and t[0] == "d"), "")
    return f"{kind}:{pubkey}:{d}"


def allowed(ev, path: Path | None = None) -> bool:
    data = load(path)
    if not data["hidden"] and not data["blocked"]:
        return True
    if ev.pubkey in data["blocked"] or ev.id in data["hidden"]:
        return False
    addr = address(ev.kind, ev.pubkey, ev.tags)
    return not (addr and addr in data["hidden"])


def _hex_pubkey(who: str) -> str:
    who = (who or "").strip()
    if who.startswith("npub1"):
        from rag.identity import _bech32_to_hex
        return _bech32_to_hex("npub", who)
    if len(who) == 64 and all(c in "0123456789abcdef" for c in who.lower()):
        return who.lower()
    raise ValueError("expected an npub or a 64-character hex key")


def hide(event_id: str, *, kind: int | None = None, pubkey: str | None = None, tags: list | None = None,
         preview: str = "", path: Path | None = None) -> str:
    """Hide one item. Returns the key it's listed under (address or id)."""
    if not (len(event_id or "") == 64 and all(c in "0123456789abcdef" for c in event_id)):
        raise ValueError("expected a 64-character event id")
    addr = address(kind, pubkey, tags or []) if kind is not None and pubkey else None
    entry = {"at": int(time.time()), "preview": (preview or "")[:140], "id": event_id}
    with _lock:
        data = load(path)
        data = {"hidden": dict(data["hidden"]), "blocked": dict(data["blocked"])}
        data["hidden"][event_id] = entry
        if addr:
            data["hidden"][addr] = entry
        _save(data, path)
    return addr or event_id


def unhide(key: str, path: Path | None = None) -> bool:
    """Unhide by id or address; removes every entry for that item."""
    with _lock:
        data = load(path)
        hidden = dict(data["hidden"])
        target = hidden.get(key, {}).get("id") or key
        drop = [k for k, v in hidden.items() if k == key or v.get("id") == target]
        for k in drop:
            hidden.pop(k)
        _save({"hidden": hidden, "blocked": dict(data["blocked"])}, path)
    return bool(drop)


def block(who: str, note: str = "", path: Path | None = None) -> str:
    pk = _hex_pubkey(who)
    with _lock:
        data = load(path)
        blocked = dict(data["blocked"])
        blocked[pk] = {"at": int(time.time()), "note": (note or "")[:140]}
        _save({"hidden": dict(data["hidden"]), "blocked": blocked}, path)
    return pk


def unblock(who: str, path: Path | None = None) -> bool:
    pk = _hex_pubkey(who)
    with _lock:
        data = load(path)
        blocked = dict(data["blocked"])
        found = blocked.pop(pk, None) is not None
        _save({"hidden": dict(data["hidden"]), "blocked": blocked}, path)
    return found


def is_blocked(pubkey: str, path: Path | None = None) -> bool:
    return pubkey in load(path)["blocked"]


def summary(path: Path | None = None) -> dict:
    """For the host's page: one row per hidden item, one per blocked key."""
    data = load(path)
    items: dict[str, dict] = {}
    for key, v in data["hidden"].items():
        row = items.setdefault(v.get("id") or key, {"id": v.get("id") or key, "at": v.get("at"),
                                                    "preview": v.get("preview", "")})
        if ":" in key:
            row["address"] = key
    from rag.identity import _hex_to_bech32
    hex_to_npub = lambda pk: _hex_to_bech32("npub", pk)  # noqa: E731
    return {"hidden": sorted(items.values(), key=lambda r: -(r["at"] or 0)),
            "blocked": sorted(({"pubkey": pk, "npub": hex_to_npub(pk), **v} for pk, v in data["blocked"].items()),
                              key=lambda r: -(r["at"] or 0))}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    try:
        if cmd == "list":
            s = summary()
            print(f"Hidden: {len(s['hidden'])}")
            for r in s["hidden"]:
                print(f"  {r['id'][:12]}…  {r['preview']}")
            print(f"Blocked: {len(s['blocked'])}")
            for r in s["blocked"]:
                print(f"  {r['npub']}  {r.get('note', '')}")
            return 0
        if cmd == "hide" and len(argv) > 1:
            hide(argv[1]); print("Hidden."); return 0
        if cmd == "unhide" and len(argv) > 1:
            print("Unhidden." if unhide(argv[1]) else "It wasn't hidden."); return 0
        if cmd == "block" and len(argv) > 1:
            block(argv[1], " ".join(argv[2:])); print("Blocked."); return 0
        if cmd == "unblock" and len(argv) > 1:
            print("Unblocked." if unblock(argv[1]) else "It wasn't blocked."); return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
