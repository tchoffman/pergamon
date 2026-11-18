"""This hub's identity, and the other hubs it trusts.

Each hub has its own key (build/state/hub.key), separate from any person's.
Hosts trust each other by exchanging hub codes:

    pergamon-hub:?k=npub1...&n=Marietta%20North&w=http://192.168.1.5:8090&r=ws://192.168.1.5:8091/

Gossip (`pergamon sync pull|push|both|all`) runs only with trusted hubs, after
the other hub proves it holds its key by signing a fresh challenge. USB
bundles carry a manifest signed by the exporting hub; a bundle from a trusted
hub imports directly, anything else is shown for review and accepted once.

    pergamon hub code              # this hub's code (give it to hosts you trust)
    pergamon hub trust <code>      # trust another hub
    pergamon hub list | untrust <name or npub>
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from rag.config import REPO_ROOT
from rag.identity import _bech32_to_hex, _hex_to_bech32, generate_keypair
from rag.nostr import Event, sign_event, verify_event

STATE = REPO_ROOT / "build" / "state"
KEY_PATH = STATE / "hub.key"
TRUST_PATH = STATE / "trusted_hubs.json"
KIND_HELLO, KIND_MANIFEST = 27235, 27236
HELLO_MAX_AGE_S = 120


class TrustError(RuntimeError):
    """Something the host should see; the message says what to do."""


# ───────────────────────────── this hub ─────────────────────────────

def _nsec() -> str:
    if KEY_PATH.exists():
        return KEY_PATH.read_text().strip()
    nsec, _, _ = generate_keypair()
    STATE.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(nsec + "\n")
    os.chmod(KEY_PATH, 0o600)
    return nsec


def pubkey() -> str:
    from rag import nip44
    return nip44.pubkey_of(_bech32_to_hex("nsec", _nsec()))


def npub() -> str:
    return _hex_to_bech32("npub", pubkey())


def name() -> str:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    return ((local.get("hub") or {}).get("name") or socket.gethostname().split(".")[0])[:60]


def _lan(url: str) -> str:
    """Swap localhost for this machine's LAN address so another hub can reach it."""
    from rag.cards import lan_ip
    ip = lan_ip()
    if not ip:
        return url
    return url.replace("localhost", ip).replace("127.0.0.1", ip)


def addresses() -> tuple[str, str]:
    from rag.nostr import _load_relays_from_config
    relay = _lan(_load_relays_from_config()[0])
    return _lan("http://localhost:8090"), relay


def code() -> str:
    web, relay = addresses()
    return "pergamon-hub:?" + urllib.parse.urlencode({"k": npub(), "n": name(), "w": web, "r": relay})


def sign(kind: int, tags: list, content: str = "") -> Event:
    return sign_event(Event(kind=kind, pubkey=pubkey(), tags=tags, content=content), _nsec())


def hello(challenge: str) -> dict:
    """Answer another hub's challenge: proves this hub holds its key."""
    if not (len(challenge) == 64 and all(c in "0123456789abcdef" for c in challenge)):
        raise ValueError("challenge must be 64 hex characters")
    ev = sign(KIND_HELLO, [["challenge", challenge], ["purpose", "hub-hello"]], name())
    return {"event": ev.to_json(), "name": name()}


# ───────────────────────────── trusted hubs ─────────────────────────────

def parse_code(text: str) -> dict:
    text = (text or "").strip()
    if not text.startswith("pergamon-hub:"):
        raise TrustError("That isn't a hub code (it should start with pergamon-hub:).")
    q = urllib.parse.parse_qs(text.split("?", 1)[-1])
    get = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    try:
        pk = _bech32_to_hex("npub", get("k"))
    except Exception:
        raise TrustError("The hub code's key is damaged; ask for it again.")
    return {"pubkey": pk, "name": get("n")[:60] or "unnamed hub", "web": get("w"), "relay": get("r")}


def trusted() -> dict[str, dict]:
    try:
        return json.loads(TRUST_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(hubs: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    TRUST_PATH.write_text(json.dumps(hubs, indent=1, sort_keys=True))


def trust(info: dict) -> dict:
    if info["pubkey"] == pubkey():
        raise TrustError("That's this hub's own code.")
    hubs = trusted()
    hubs[info["pubkey"]] = {**{k: info.get(k, "") for k in ("name", "web", "relay")}, "added": int(time.time())}
    _save(hubs)
    return hubs[info["pubkey"]]


def untrust(who: str) -> bool:
    hubs = trusted()
    key = next((pk for pk, h in hubs.items()
                if who in (pk, h["name"]) or (who.startswith("npub1") and _hex_to_bech32("npub", pk) == who)), None)
    if key:
        hubs.pop(key)
        _save(hubs)
    return key is not None


def find(remote: str) -> tuple[str, dict] | None:
    """A trusted hub by name, relay URL or web URL."""
    norm = lambda u: (u or "").rstrip("/").lower()  # noqa: E731
    for pk, h in trusted().items():
        if remote == h["name"] or norm(remote) in (norm(h["relay"]), norm(h["web"])):
            return pk, h
    return None


def verify_remote(remote: str, fetch=None) -> tuple[str, dict]:
    """The trusted hub `remote` names, after it signs a fresh challenge.
    Raises TrustError (with what to do) if it isn't trusted or can't prove it."""
    hit = find(remote)
    if not hit:
        raise TrustError(f"{remote} isn't a trusted hub, so this hub won't sync with it. "
                         "Ask its host for their hub code and run: pergamon hub trust '<code>'")
    pk, h = hit
    challenge = secrets.token_hex(32)
    url = h["web"].rstrip("/") + "/api/hub/hello?challenge=" + challenge
    try:
        if fetch:
            reply = fetch(url)
        else:
            with urllib.request.urlopen(url, timeout=15) as r:
                reply = json.load(r)
        raw = reply["event"]
        ev = Event(kind=raw["kind"], content=raw["content"], pubkey=raw["pubkey"], tags=raw["tags"],
                   created_at=raw["created_at"], id=raw["id"], sig=raw["sig"])
    except Exception as e:  # noqa: BLE001
        raise TrustError(f"Couldn't reach {h['name']} at {h['web']} to check who it is ({e}).")
    ok = (verify_event(ev) and ev.pubkey == pk and ev.kind == KIND_HELLO
          and ["challenge", challenge] in [list(t) for t in ev.tags]
          and abs(time.time() - ev.created_at) < HELLO_MAX_AGE_S)
    if not ok:
        raise TrustError(f"The hub at {h['web']} couldn't prove it is {h['name']}. "
                         "It may have been replaced; check with its host before trusting it again.")
    return pk, h


# ───────────────────────────── bundles ─────────────────────────────

def bundle_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def manifest(lines: list[str]) -> Event:
    web, relay = addresses()
    return sign(KIND_MANIFEST, [["sha256", bundle_hash(lines)], ["count", str(len(lines))]],
                json.dumps({"name": name(), "web": web, "relay": relay, "made": int(time.time())}))


def check_manifest(raw: dict | None, lines: list[str]) -> dict:
    """Who signed a bundle and whether it's intact: {signed, intact, pubkey,
    name, trusted, web, relay}."""
    out = {"signed": False, "intact": False, "pubkey": None, "name": None, "trusted": False,
           "web": "", "relay": ""}
    if not raw:
        return out
    try:
        ev = Event(kind=raw["kind"], content=raw["content"], pubkey=raw["pubkey"], tags=raw["tags"],
                   created_at=raw["created_at"], id=raw["id"], sig=raw["sig"])
        meta = json.loads(ev.content)
    except (KeyError, TypeError, ValueError):
        return out
    if ev.kind != KIND_MANIFEST or not verify_event(ev):
        return out
    tags = {t[0]: t[1] for t in ev.tags if len(t) > 1}
    out.update(signed=True, pubkey=ev.pubkey, name=str(meta.get("name", ""))[:60],
               web=str(meta.get("web", "")), relay=str(meta.get("relay", "")),
               intact=tags.get("sha256") == bundle_hash(lines) and tags.get("count") == str(len(lines)))
    out["trusted"] = out["intact"] and ev.pubkey in trusted()
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    try:
        if cmd == "code":
            c = code()
            print(c)
            print(f"\nThis hub: {name()} ({npub()})")
            print("Give this code to hosts you trust; they run: pergamon hub trust '<code>'")
            return 0
        if cmd == "trust" and len(argv) > 1:
            info = parse_code(" ".join(argv[1:]))
            trust(info)
            print(f"Trusted {info['name']} ({_hex_to_bech32('npub', info['pubkey'])}).")
            return 0
        if cmd == "untrust" and len(argv) > 1:
            print("Removed." if untrust(argv[1]) else "No trusted hub by that name.")
            return 0
        if cmd == "list":
            hubs = trusted()
            print(f"This hub: {name()} ({npub()})")
            print(f"Trusted hubs: {len(hubs)}")
            for pk, h in hubs.items():
                print(f"  {h['name']:<24} {h['relay']:<32} {_hex_to_bech32('npub', pk)[:20]}…")
            return 0
    except TrustError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
