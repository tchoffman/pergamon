"""Listen for Meshtastic DMs and reply with a RAG-chunked answer.

Real-hardware path. Needs a Meshtastic device attached via USB (the serial
interface) or reachable over TCP.

Safety:
- Only answers DMs (private channel). Broadcast traffic is ignored.
- Optionally restrict to an allowlist of node IDs (see config/install.local.yaml
  under comms.meshtastic.allowed_node_ids).
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from collections import OrderedDict
from typing import Any

from rich.console import Console

from modules.comms.meshtastic.common import ask
from rag.install_config import load_yaml, INSTALL_LOCAL_PATH

console = Console()


def _load_allowed_nodes() -> set[str]:
    cfg = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    allowed = (((cfg.get("comms") or {}).get("meshtastic") or {}).get("allowed_node_ids")) or []
    return {str(x) for x in allowed}


ACK_TIMEOUT_S = 30
ATTEMPTS = 3


def _send_confirmed(iface: Any, dest_id: str, text: str) -> bool:
    """Send one part and block until the receiver acknowledges it, retrying.

    Firing parts back to back loses all but the first: each LoRa packet takes
    over a second of airtime. Waiting for the ack paces the parts to the link.
    """
    for attempt in range(1, ATTEMPTS + 1):
        done = threading.Event()
        result: dict = {}

        # The name matters: meshtastic delivers plain ACKs only to a callback
        # called onAckNak.
        def onAckNak(packet: dict) -> None:
            result["error"] = ((packet.get("decoded") or {}).get("routing") or {}).get("errorReason", "NONE")
            done.set()

        iface.sendText(text, destinationId=dest_id, wantAck=True, onResponse=onAckNak)
        if done.wait(ACK_TIMEOUT_S) and result.get("error") == "NONE":
            return True
        console.print(f"[yellow]  part not confirmed ({result.get('error', 'timeout')}), "
                      f"attempt {attempt}/{ATTEMPTS}[/yellow]")
    return False


def _reply(iface: Any, dest_id: str, reply):
    chunks = reply.chunks()
    console.print(f"[dim]  → {len(chunks)} LoRa packet(s)[/dim]")
    console.print(f"[green]  {reply.answer}[/green]" + (f" [dim]({reply.sources[0]})[/dim]" if reply.sources else ""))
    for i, c in enumerate(chunks, 1):
        if not _send_confirmed(iface, dest_id, c):
            console.print(f"[red]  giving up at part {i}/{len(chunks)}[/red]")
            return
        console.print(f"[dim]  ✓ part {i}/{len(chunks)}[/dim]")


MAX_QUESTION_CHARS = 500     # a LoRa text is ~200; anything longer is junk
MAX_PENDING_PER_SENDER = 2   # one radio can't fill the queue for everyone else
SEEN_IDS_KEPT = 200
RECONNECT_DELAY_S = 10

HELP_TEXT = ("Pergamon offline library. DM me any question and I'll answer in "
             "numbered parts. 'ping' tests the link.")
SORRY_TEXT = "[pergamon] Sorry, I couldn't answer that just now. Try again shortly."
BUSY_TEXT = "[pergamon] Still working on your earlier questions; send this again after they arrive."

# Replies run on one worker thread: the receive callback must return quickly,
# because acks for our own sends arrive on that same thread.
_jobs: "queue.Queue[tuple[Any, str, str]]" = queue.Queue()
_pending: dict[str, int] = {}
_seen: "OrderedDict[Any, None]" = OrderedDict()
_lock = threading.Lock()


def _quick_reply(text: str) -> str | None:
    """Answers that don't need the model (link tests, help)."""
    t = text.strip().lower().rstrip("?!.")
    if t == "ping":
        return "pong"
    if t in ("help", "?", ""):
        return HELP_TEXT
    return None


def _worker() -> None:
    while True:
        interface, sender, text = _jobs.get()
        try:
            quick = _quick_reply(text)
            if quick is not None:
                _send_confirmed(interface, sender, quick)
            else:
                _reply(interface, sender, ask(text))
        except Exception as e:
            # Details stay in the local log; the radio gets a plain apology.
            console.print(f"[red]ask failed: {e!r}[/red]")
            try:
                _send_confirmed(interface, sender, SORRY_TEXT)
            except Exception as e2:
                console.print(f"[red]could not send apology: {e2!r}[/red]")
        finally:
            with _lock:
                _pending[sender] = max(0, _pending.get(sender, 1) - 1)


def _already_seen(packet: dict) -> bool:
    """A retransmitted message arrives again with the same id; answer it once."""
    key = (packet.get("from"), packet.get("id"))
    if key[1] is None:
        return False
    with _lock:
        if key in _seen:
            return True
        _seen[key] = None
        while len(_seen) > SEEN_IDS_KEPT:
            _seen.popitem(last=False)
    return False


def on_receive(packet: dict, interface) -> None:
    """Called by Meshtastic's pubsub for each received packet."""
    decoded = packet.get("decoded") or {}
    if decoded.get("portnum") != "TEXT_MESSAGE_APP":
        return
    text = (decoded.get("text") or "").strip()
    sender = packet.get("fromId") or str(packet.get("from"))
    channel = packet.get("channel", 0)

    # Only DMs — channel 0 broadcast would spam the whole mesh.
    to_id = packet.get("toId") or str(packet.get("to"))
    is_dm = to_id and to_id != "^all"
    if not is_dm:
        return

    allowed = _load_allowed_nodes()
    if allowed and sender not in allowed:
        console.print(f"[yellow]ignoring DM from unallowed node {sender}[/yellow]")
        return

    if _already_seen(packet):
        console.print(f"[dim]duplicate from {sender} ignored[/dim]")
        return

    text = text[:MAX_QUESTION_CHARS]
    console.print(f"[bold]{sender}[/bold] (ch {channel}): {text}")
    with _lock:
        busy = _pending.get(sender, 0) >= MAX_PENDING_PER_SENDER
        if not busy:
            _pending[sender] = _pending.get(sender, 0) + 1
    if busy:
        console.print(f"[yellow]  {sender} has too many pending questions; told them to wait[/yellow]")
        interface.sendText(BUSY_TEXT, destinationId=sender)
        return
    _jobs.put((interface, sender, text))


def main() -> int:
    try:
        import meshtastic.serial_interface as serial_iface
    except ImportError:
        console.print("[red]meshtastic package not installed[/red]")
        return 1
    try:
        from pubsub import pub
    except ImportError:
        console.print("[red]pypubsub not installed (comes with meshtastic)[/red]")
        return 1

    port = os.environ.get("MESHTASTIC_PORT")  # /dev/tty.usbserial-* on mac

    def open_radio():
        console.print(f"[cyan]opening Meshtastic serial interface[/cyan] (port={port or 'auto'})")
        return serial_iface.SerialInterface(devPath=port) if port else serial_iface.SerialInterface()

    try:
        iface = open_radio()
    except Exception as e:
        console.print(f"[red]no device found: {e}[/red]")
        console.print("Try:  pergamon mesh mock      # develop without a radio")
        return 1

    allowed = _load_allowed_nodes()
    if allowed:
        console.print(f"[dim]allowed node IDs: {sorted(allowed)}[/dim]")
    else:
        console.print("[yellow]no allowlist set — responding to all DMs[/yellow]")

    lost = threading.Event()

    def on_lost(interface, topic=pub.AUTO_TOPIC) -> None:
        lost.set()

    threading.Thread(target=_worker, daemon=True).start()
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_lost, "meshtastic.connection.lost")

    console.print("[green]ready. ctrl-c to quit.[/green]")
    try:
        while True:
            # A dropped USB cable or radio reboot shows up as connection.lost;
            # keep trying to reopen instead of sitting deaf.
            if lost.wait(1):
                console.print("[red]radio connection lost; reconnecting...[/red]")
                try:
                    iface.close()
                except Exception:
                    pass
                while True:
                    time.sleep(RECONNECT_DELAY_S)
                    try:
                        iface = open_radio()
                        lost.clear()
                        console.print("[green]reconnected. ready.[/green]")
                        break
                    except Exception as e:
                        console.print(f"[dim]  still no radio ({e}); retrying[/dim]")
    except KeyboardInterrupt:
        console.print("\n[dim]closing[/dim]")
        iface.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
