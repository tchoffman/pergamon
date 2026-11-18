"""Hub accounts: sign in with a username and password; a Nostr key underneath.

The password never reaches the hub. The phone derives a key from it with
scrypt (NIP-49), uses that to lock the person's private key into an
`ncryptsec1...` string, and sends the hub:

  - the locked key (or nothing, if they keep it only on their phone),
  - the scrypt salt and cost, which the hub hands back at sign-in,
  - a sign-in token: sha256 of the scrypt key. The hub stores only a hash of
    that, and gives the locked key back only to someone who presents it.
  - a note signed by the key, proving the account really owns that key.

So the hub can't read anyone's key. Whoever runs the hub does control the
page people type their password into, though; the sign-in page says so.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rag.config import REPO_ROOT

DB_PATH = REPO_ROOT / "build" / "state" / "accounts.db"
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,30}$")
MIN_LOG_N, MAX_LOG_N = 14, 22       # scrypt cost range a phone can do and a guesser can't
PROOF_MAX_AGE_S = 600
FAILS_PER_WINDOW, FAIL_WINDOW_S = 8, 15 * 60


class AccountError(ValueError):
    """Something the person can fix; the message is shown to them."""


@dataclass
class Account:
    username: str
    pubkey: str
    name: str
    salt: str
    log_n: int
    ncryptsec: str | None


def _hash_token(token: str) -> str:
    return hashlib.sha256(bytes.fromhex(token)).hexdigest()


class Accounts:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS accounts (
                username TEXT PRIMARY KEY, pubkey TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                salt TEXT NOT NULL, log_n INTEGER NOT NULL, auth_hash TEXT NOT NULL,
                ncryptsec TEXT, created_at INTEGER NOT NULL)""")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    # ── sign-up ──
    def create(self, username: str, *, pubkey: str, name: str, salt: str, log_n: int,
               auth: str, ncryptsec: str | None) -> Account:
        username = normalize(username)
        if not re.fullmatch(r"[0-9a-f]{32}", salt or ""):
            raise AccountError("bad salt")
        if not MIN_LOG_N <= int(log_n) <= MAX_LOG_N:
            raise AccountError("password lock too weak or too slow")
        if not re.fullmatch(r"[0-9a-f]{64}", auth or ""):
            raise AccountError("bad sign-in token")
        if ncryptsec is not None and not re.fullmatch(r"ncryptsec1[02-9ac-hj-np-z]{150,170}", ncryptsec):
            raise AccountError("bad locked key")
        with self._lock, self._db() as db:
            if db.execute("SELECT 1 FROM accounts WHERE username=?", (username,)).fetchone():
                raise AccountError("That username is taken on this hub.")
            if db.execute("SELECT 1 FROM accounts WHERE pubkey=?", (pubkey,)).fetchone():
                raise AccountError("This key already has an account here. Sign in instead.")
            db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)",
                       (username, pubkey, name[:60], salt, int(log_n), _hash_token(auth),
                        ncryptsec, int(time.time())))
        return Account(username, pubkey, name[:60], salt, int(log_n), ncryptsec)

    # ── sign-in ──
    def get(self, username: str) -> Account | None:
        with self._db() as db:
            row = db.execute("SELECT username, pubkey, name, salt, log_n, ncryptsec FROM accounts "
                             "WHERE username=?", (normalize(username, strict=False),)).fetchone()
        return Account(*row) if row else None

    def by_pubkey(self, pubkey: str) -> Account | None:
        with self._db() as db:
            row = db.execute("SELECT username, pubkey, name, salt, log_n, ncryptsec FROM accounts "
                             "WHERE pubkey=?", (pubkey,)).fetchone()
        return Account(*row) if row else None

    def sign_in(self, username: str, auth: str, client: str, now: float | None = None) -> Account:
        """The account, if `auth` is right. Repeated wrong tries from one device,
        or against one account, are refused for a while (guessing online is
        the only way to guess at all)."""
        now = now or time.time()
        username = normalize(username, strict=False)
        for k in (f"u:{username}", f"c:{client}"):
            recent = [t for t in self._fails.get(k, []) if now - t < FAIL_WINDOW_S]
            self._fails[k] = recent
            if len(recent) >= FAILS_PER_WINDOW:
                raise AccountError("Too many wrong passwords. Wait 15 minutes and try again.")
        with self._db() as db:
            row = db.execute("SELECT auth_hash FROM accounts WHERE username=?", (username,)).fetchone()
        ok = bool(row) and re.fullmatch(r"[0-9a-f]{64}", auth or "") and \
            hmac.compare_digest(row[0], _hash_token(auth))
        if not ok:
            for k in (f"u:{username}", f"c:{client}"):
                self._fails.setdefault(k, []).append(now)
            raise AccountError("Wrong username or password.")
        return self.get(username)


def normalize(username: str, strict: bool = True) -> str:
    u = (username or "").strip().lower()
    if strict and not USERNAME.fullmatch(u):
        raise AccountError("Usernames are 2-31 letters, numbers, dots, dashes or underscores.")
    return u


def check_proof(proof: dict, username: str, now: float | None = None) -> str:
    """The pubkey that signed this sign-up note, if it's valid and fresh."""
    from rag.nostr import Event, verify_event
    try:
        ev = Event(kind=int(proof["kind"]), content=str(proof["content"]), pubkey=str(proof["pubkey"]),
                   tags=list(proof["tags"]), created_at=int(proof["created_at"]),
                   id=str(proof["id"]), sig=str(proof["sig"]))
    except (KeyError, TypeError, ValueError):
        raise AccountError("missing proof that you hold this key")
    if not verify_event(ev):
        raise AccountError("the key proof doesn't verify")
    if ev.kind != 27235 or ["username", username] not in [list(t) for t in ev.tags]:
        raise AccountError("the key proof is for something else")
    if abs((now or time.time()) - ev.created_at) > PROOF_MAX_AGE_S:
        raise AccountError("the key proof is stale; check this device's clock")
    return ev.pubkey


store: Accounts | None = None


def get_store() -> Accounts:
    global store
    if store is None:
        store = Accounts()
    return store
