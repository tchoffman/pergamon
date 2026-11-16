"""Two-radio test: play the handheld user over Bluetooth, check what arrives.

The Pergamon radio runs `pergamon mesh listen` (USB). This script connects to
the *other* radio over Bluetooth, sends DMs to Pergamon like a person with a
handheld would, and checks the reply that actually arrives on the handheld:
every [i/N] part present, in order, within a time limit.

    build/venv/bin/python scripts/radio-test.py --ble <address> --to '!e06e2b0c'
    build/venv/bin/python scripts/radio-test.py --ble <address> --to '!e06e2b0c' --only ping

Find the handheld's Bluetooth address with `meshtastic --ble-scan`. The phone
app must be disconnected from it first (one Bluetooth client at a time).
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time

CASES = [
    # (id, message to send, check on the reassembled reply, seconds to wait)
    ("ping", "ping", lambda r: r.strip() == "pong", 60),
    ("help", "help", lambda r: "/news" in r and "/msg" in r, 60),
    ("unknown-cmd", "/bogus", lambda r: "Unknown command" in r, 60),
    ("math", "What is 15% of 240?", lambda r: "36" in r, 180),
    ("general", "Explain photosynthesis in one sentence.",
     lambda r: "light" in r.lower(), 180),
    ("weather", "What's the weather today?",
     lambda r: any(w in r.lower() for w in ("don't have", "do not have", "no live", "can't",
                                            "cannot", "no access", "unable")), 180),
    ("snakebite", "How do I treat a snake bite?",
     lambda r: "cut" in r.lower() and any(w in r.lower() for w in ("medical", "hospital", "emergency")), 240),
]

PART = re.compile(r"^\[(\d+)/(\d+)\]\s?(.*)$", re.S)


class Inbox:
    """Collects text DMs from one node and reassembles [i/N] parts."""

    def __init__(self, from_id: str):
        self.from_id = from_id
        self.parts: dict[int, str] = {}
        self.total: int | None = None
        self.order: list[int] = []
        self.event = threading.Event()

    def reset(self) -> None:
        self.parts, self.total, self.order = {}, None, []
        self.event.clear()

    def on_receive(self, packet, interface=None) -> None:
        d = packet.get("decoded") or {}
        if d.get("portnum") != "TEXT_MESSAGE_APP" or packet.get("fromId") != self.from_id:
            return
        text = d.get("text") or ""
        m = PART.match(text)
        if not m:
            self.parts, self.total = {1: text}, 1
            self.order.append(1)
        else:
            i, n, body = int(m.group(1)), int(m.group(2)), m.group(3)
            self.parts[i], self.total = body, n
            self.order.append(i)
        if self.total and len(self.parts) >= self.total:
            self.event.set()

    def text(self) -> str:
        return " ".join(self.parts[i] for i in sorted(self.parts))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ble", required=True, help="handheld's Bluetooth address")
    ap.add_argument("--to", required=True, help="Pergamon radio node id, e.g. !e06e2b0c")
    ap.add_argument("--only", action="append", help="case id (repeatable)")
    args = ap.parse_args()

    from meshtastic.ble_interface import BLEInterface
    from pubsub import pub

    inbox = Inbox(args.to)
    pub.subscribe(inbox.on_receive, "meshtastic.receive")
    print(f"connecting to handheld over Bluetooth ({args.ble})...", flush=True)
    radio = BLEInterface(args.ble)
    print(f"connected: {radio.getMyUser().get('longName')}", flush=True)

    cases = [c for c in CASES if not args.only or c[0] in args.only]
    failures = 0
    try:
        for cid, msg, ok, wait_s in cases:
            inbox.reset()
            t = time.time()
            radio.sendText(msg, destinationId=args.to, wantAck=True)
            complete = inbox.event.wait(wait_s)
            took = time.time() - t
            reply = inbox.text()
            in_order = inbox.order == sorted(inbox.order)
            passed = complete and ok(reply)
            failures += not passed
            got = f"{len(inbox.parts)}/{inbox.total or '?'} parts" + ("" if in_order else ", out of order")
            print(f"{'PASS' if passed else 'FAIL'}  {cid:12} {took:5.0f}s  {got}  {reply[:110]!r}", flush=True)
            time.sleep(3)  # let the listener settle between questions
    finally:
        radio.close()
    print(f"{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
