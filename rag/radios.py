"""Which radios are Pergamon's: the host's decision per radio, by node ID.

Plug any Meshtastic radio into the hub. The radio listener identifies it:
  - a "server" radio answers questions, commands and mail for Pergamon;
  - a "client" radio (someone's handheld being charged, a spare) is left alone;
  - a radio not seen before waits until the host decides, on the Radios page
    or with `pergamon radios serve|client <id>`.
Several server radios can run at once. Decisions are remembered, so a radio
gets the same role whenever and wherever it's plugged in.

    pergamon radios                 # connected radios and their roles
    pergamon radios serve <id>      # make it a Pergamon radio
    pergamon radios client <id>     # leave it alone
    pergamon radios forget <id>     # ask again next time
    pergamon radios preset longfast # switch every radio on USB (e.g. mediumfast at home)
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from rag.config import REPO_ROOT

STATE = REPO_ROOT / "build" / "state"
PATH = STATE / "radios.json"
CONNECTED_PATH = STATE / "radios_connected.json"   # written by the listener
REQUEST_PATH = STATE / "radios_request.json"       # read by the listener
# Presets people actually pick; every radio in a mesh must use the same one.
PRESETS = {"longfast": "LONG_FAST", "mediumfast": "MEDIUM_FAST", "mediumslow": "MEDIUM_SLOW",
           "longmoderate": "LONG_MODERATE", "shortfast": "SHORT_FAST", "shortslow": "SHORT_SLOW"}
ROLES = ("server", "client")
_lock = threading.Lock()


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


def known() -> dict[str, dict]:
    data = _read(PATH)
    if not data:
        data = _migrate()
    return data


def _migrate() -> dict:
    """Carry over the older single-radio setting (comms.meshtastic.hub_node_id)."""
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    hub = ((local.get("comms") or {}).get("meshtastic") or {}).get("hub_node_id")
    if not hub:
        return {}
    data = {hub: {"role": "server", "name": "", "decided": int(time.time())}}
    _write(PATH, data)
    return data


def role(node_id: str) -> str | None:
    """'server', 'client', or None if the host hasn't decided."""
    return (known().get(node_id) or {}).get("role")


def set_role(node_id: str, new_role: str, name: str = "") -> dict:
    if new_role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    node_id = _normalize(node_id)
    with _lock:
        data = known()
        entry = data.get(node_id, {})
        data[node_id] = {"role": new_role, "name": name or entry.get("name", ""), "decided": int(time.time())}
        _write(PATH, data)
    return data[node_id]


def forget(node_id: str) -> bool:
    node_id = _normalize(node_id)
    with _lock:
        data = known()
        found = data.pop(node_id, None) is not None
        _write(PATH, data)
    return found


def _normalize(node_id: str) -> str:
    node_id = (node_id or "").strip()
    if not node_id.startswith("!"):
        node_id = "!" + node_id
    if len(node_id) != 9 or any(c not in "0123456789abcdef" for c in node_id[1:].lower()):
        raise ValueError("a node ID looks like !e06e2b0c")
    return node_id.lower()


def connected() -> list[dict]:
    """Radios on USB right now, as the listener last saw them."""
    data = _read(CONNECTED_PATH)
    if not data or time.time() - data.get("updated", 0) > 120:
        return []  # the listener isn't running
    regs = known()
    return [{**r, "role": (regs.get(r["id"]) or {}).get("role")} for r in data.get("radios", [])]


def request_preset(name: str) -> str:
    """Ask the listener to switch every radio on USB to a modem preset."""
    key = name.lower().replace("_", "").replace("-", "").replace(" ", "")
    if key not in PRESETS:
        raise ValueError(f"preset must be one of {', '.join(PRESETS)}")
    _write(REQUEST_PATH, {"preset": PRESETS[key], "at": int(time.time())})
    return PRESETS[key]


def take_request() -> dict | None:
    data = _read(REQUEST_PATH)
    if data:
        REQUEST_PATH.unlink(missing_ok=True)
    return data or None


def write_connected(radios: list[dict]) -> None:
    _write(CONNECTED_PATH, {"updated": int(time.time()), "radios": radios})


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    try:
        if cmd == "list":
            now = connected()
            if not now:
                print("No radios reported (is `pergamon mesh listen` running?).")
            for r in now:
                print(f"  {r['port']:<24} {r['name']:<20} {r['id']}  {r['role'] or 'NOT DECIDED':<12} "
                      f"{r.get('preset') or ''}")
            others = {k: v for k, v in known().items() if k not in {r['id'] for r in now}}
            if others:
                print("Known, not connected:")
                for k, v in others.items():
                    print(f"  {v.get('name') or '':<20} {k}  {v['role']}")
            return 0
        if cmd in ("serve", "client") and len(argv) > 1:
            entry = set_role(argv[1], "server" if cmd == "serve" else "client")
            print(f"{argv[1]} is now a {'Pergamon (server)' if cmd == 'serve' else 'client'} radio."
                  + (" The listener picks it up within a few seconds." if cmd == "serve" else ""))
            return 0
        if cmd == "preset" and len(argv) > 1:
            p = request_preset(argv[1])
            print(f"Asked the listener to switch every radio on USB to {p}. Each restarts (about a "
                  "minute); check with `pergamon radios`. Radios not on USB must be switched separately.")
            return 0
        if cmd == "forget" and len(argv) > 1:
            print("Forgotten." if forget(argv[1]) else "Not a known radio.")
            return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
