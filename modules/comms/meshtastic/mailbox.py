"""Store-and-forward mail between radios, held by the always-on Pergamon node.

Handhelds drift in and out of range; Pergamon sits still. A radio user sends
"/msg <who> <text>" and Pergamon keeps it in SQLite until it next hears any
packet from <who>'s radio, then delivers it as a DM.

Limits keep one sender from filling the box: text is capped, each sender may
have a few undelivered messages, each recipient holds a few, and mail expires.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from rag.config import REPO_ROOT

DB_PATH = REPO_ROOT / "build" / "mesh" / "mailbox.db"
MAX_TEXT = 180
MAX_PENDING_FROM_SENDER = 10
MAX_PENDING_FOR_RECIPIENT = 5
EXPIRE_DAYS = 7


class MailboxError(ValueError):
    """A user-facing reason the message wasn't accepted."""


class Mailbox:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS mail (
            id INTEGER PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
            text TEXT NOT NULL, created_at INTEGER NOT NULL, delivered_at INTEGER)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS mail_pending ON mail(recipient, delivered_at)")
        self.db.commit()

    def _expire(self, now: float) -> None:
        self.db.execute("DELETE FROM mail WHERE delivered_at IS NULL AND created_at < ?",
                        (int(now - EXPIRE_DAYS * 86400),))

    def deposit(self, sender: str, recipient: str, text: str, now: float | None = None) -> int:
        now = now or time.time()
        text = " ".join(text.split())
        if not text:
            raise MailboxError("Message is empty.")
        if recipient == sender:
            raise MailboxError("That's your own radio.")
        self._expire(now)
        (from_sender,) = self.db.execute(
            "SELECT COUNT(*) FROM mail WHERE sender=? AND delivered_at IS NULL", (sender,)).fetchone()
        if from_sender >= MAX_PENDING_FROM_SENDER:
            raise MailboxError(f"You have {from_sender} undelivered messages waiting; try later.")
        (for_recipient,) = self.db.execute(
            "SELECT COUNT(*) FROM mail WHERE recipient=? AND delivered_at IS NULL", (recipient,)).fetchone()
        if for_recipient >= MAX_PENDING_FOR_RECIPIENT:
            raise MailboxError("Their mailbox is full; try later.")
        cur = self.db.execute(
            "INSERT INTO mail (sender, recipient, text, created_at) VALUES (?,?,?,?)",
            (sender, recipient, text[:MAX_TEXT], int(now)))
        self.db.commit()
        return cur.lastrowid

    def pending_for(self, recipient: str, now: float | None = None) -> list[dict]:
        self._expire(now or time.time())
        rows = self.db.execute(
            "SELECT id, sender, text, created_at FROM mail WHERE recipient=? AND delivered_at IS NULL "
            "ORDER BY id", (recipient,)).fetchall()
        return [dict(zip(("id", "sender", "text", "created_at"), r)) for r in rows]

    def mark_delivered(self, mail_id: int, now: float | None = None) -> None:
        self.db.execute("UPDATE mail SET delivered_at=? WHERE id=?", (int(now or time.time()), mail_id))
        self.db.commit()

    def recipients_waiting(self, now: float | None = None) -> list[str]:
        self._expire(now or time.time())
        return [r for (r,) in self.db.execute(
            "SELECT DISTINCT recipient FROM mail WHERE delivered_at IS NULL")]

    def has_pending(self, recipient: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM mail WHERE recipient=? AND delivered_at IS NULL LIMIT 1",
            (recipient,)).fetchone() is not None


def resolve_recipient(who: str, nodes: dict) -> str:
    """Turn '!a1b2c3d4', a short name, or a long name into a node id.

    `nodes` is meshtastic's interface.nodes: {id: {"user": {"id", "longName", "shortName"}}}.
    """
    who = who.strip()
    if who.startswith("!") and len(who) == 9:
        return who.lower()
    want = who.lower()
    matches = []
    for node in nodes.values():
        u = node.get("user") or {}
        names = {(u.get("shortName") or "").lower(), (u.get("longName") or "").lower()}
        if want in names and u.get("id"):
            matches.append(u["id"])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise MailboxError(f"Don't know a radio called '{who}'. Use its !id from the node list.")
    raise MailboxError(f"Several radios are called '{who}'; use the !id instead.")


def parse_msg(text: str) -> tuple[str, str] | None:
    """'/msg <who> <text>' → (who, text); None if not a /msg command."""
    t = text.strip()
    if not t.lower().startswith("/msg"):
        return None
    parts = t.split(None, 2)
    if len(parts) < 3:
        raise MailboxError("Usage: /msg <name or !id> <message>")
    return parts[1], parts[2]


def format_delivery(mail: dict, sender_name: str, now: float | None = None) -> str:
    age = max(0, int((now or time.time()) - mail["created_at"]))
    when = f"{age // 60}m" if age < 3600 else f"{age // 3600}h" if age < 86400 else f"{age // 86400}d"
    return f"Mail from {sender_name} ({when} ago): {mail['text']}"
