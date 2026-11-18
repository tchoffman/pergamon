"""NIP-17 private messages in Python: the same format the browser uses.

rumor (kind 14, unsigned) → seal (kind 13, signed by the sender, NIP-44
encrypted to the recipient) → gift wrap (kind 1059, signed by a throwaway key,
tagged only with the recipient). Used by tests and by tools that send or read
messages from the hub machine.
"""
from __future__ import annotations

import json
import os
import random
import time

from rag import nip44
from rag.identity import _bech32_to_hex, _hex_to_bech32
from rag.nostr import Event, sign_event, verify_event

KIND_RUMOR, KIND_SEAL, KIND_WRAP = 14, 13, 1059


def _blurred() -> int:
    return int(time.time()) - random.randint(0, 2 * 86400)


def _rumor_id(r: dict) -> str:
    import hashlib
    serial = json.dumps([0, r["pubkey"], r["created_at"], r["kind"], r["tags"], r["content"]],
                        separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serial.encode()).hexdigest()


def _wrap(rumor: dict, sender_sec: str, recipient: str) -> Event:
    seal = Event(kind=KIND_SEAL, pubkey=rumor["pubkey"], tags=[], created_at=_blurred(),
                 content=nip44.encrypt(json.dumps(rumor), nip44.conversation_key(sender_sec, recipient)))
    sign_event(seal, _hex_to_bech32("nsec", sender_sec))
    eph = os.urandom(32).hex()
    wrap = Event(kind=KIND_WRAP, pubkey=nip44.pubkey_of(eph), tags=[["p", recipient]], created_at=_blurred(),
                 content=nip44.encrypt(json.dumps(seal.to_json()), nip44.conversation_key(eph, recipient)))
    return sign_event(wrap, _hex_to_bech32("nsec", eph))


def build(sender_nsec: str, recipient: str, text: str) -> list[Event]:
    """Wraps for the recipient and a copy for the sender."""
    sec = _bech32_to_hex("nsec", sender_nsec)
    me = nip44.pubkey_of(sec)
    rumor = {"pubkey": me, "created_at": int(time.time()), "kind": KIND_RUMOR,
             "tags": [["p", recipient]], "content": text}
    rumor["id"] = _rumor_id(rumor)
    wraps = [_wrap(rumor, sec, recipient)]
    if recipient != me:
        wraps.append(_wrap(rumor, sec, me))
    return wraps


def _event(d: dict) -> Event:
    return Event(kind=d["kind"], content=d["content"], pubkey=d["pubkey"], tags=d["tags"],
                 created_at=d["created_at"], id=d["id"], sig=d["sig"])


def open_wrap(my_nsec: str, wrap: Event) -> dict:
    """Decrypt a wrap addressed to me. Raises ValueError on anything off,
    including a seal signed by someone other than the message's author."""
    sec = _bech32_to_hex("nsec", my_nsec)
    if wrap.kind != KIND_WRAP or not verify_event(wrap):
        raise ValueError("not a valid gift wrap")
    seal = _event(json.loads(nip44.decrypt(wrap.content, nip44.conversation_key(sec, wrap.pubkey))))
    if seal.kind != KIND_SEAL or not verify_event(seal):
        raise ValueError("seal signature doesn't verify")
    rumor = json.loads(nip44.decrypt(seal.content, nip44.conversation_key(sec, seal.pubkey)))
    if rumor["pubkey"] != seal.pubkey:
        raise ValueError("sender mismatch: forged message")
    if rumor["kind"] != KIND_RUMOR or _rumor_id(rumor) != rumor["id"]:
        raise ValueError("not a chat message")
    return {"id": rumor["id"], "from": rumor["pubkey"], "text": rumor["content"],
            "created_at": rumor["created_at"],
            "to": [t[1] for t in rumor["tags"] if t[0] == "p"]}
