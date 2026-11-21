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
from pathlib import Path
from collections import OrderedDict
from typing import Any

from rich.console import Console

from modules.comms.meshtastic import commands, mailbox
from modules.comms.meshtastic.common import MeshReply, ask
from rag.conversations import store as conversations
from rag.install_config import load_yaml, INSTALL_LOCAL_PATH

LOG_PATH = Path(__file__).resolve().parents[3] / "build" / "mesh" / "listener.log"


class _TeeConsole:
    """Prints to the terminal as usual and appends a timestamped plain-text
    copy to build/mesh/listener.log, so the host can `tail -f` it anywhere."""

    def __init__(self, path: Path = LOG_PATH):
        self.screen = Console()
        self.path = path

    def print(self, *args, **kwargs) -> None:
        self.screen.print(*args, **kwargs)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                text = Console(file=f, width=200, no_color=True, highlight=False)
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                text.print(stamp, *args, **{k: v for k, v in kwargs.items() if k != "style"})
        except OSError:
            pass  # the log is a convenience; never let it break the listener


console = _TeeConsole()


def _load_allowed_nodes() -> set[str]:
    cfg = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    allowed = (((cfg.get("comms") or {}).get("meshtastic") or {}).get("allowed_node_ids")) or []
    return {str(x) for x in allowed}


ACK_TIMEOUT_S = 30
ATTEMPTS = 3
# Pause between parts of a reply. Sent the moment part 1's ack arrived, part 2
# was lost on the air every time in tests (the receiver is still transmitting
# just after acking), costing a 30 s timeout and a resend.
PART_GAP_S = float(os.environ.get("PERGAMON_PART_GAP_S", "3"))


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
        if i > 1:
            time.sleep(PART_GAP_S)
        if not _send_confirmed(iface, dest_id, c):
            console.print(f"[red]  giving up at part {i}/{len(chunks)}[/red]")
            return
        console.print(f"[dim]  ✓ part {i}/{len(chunks)}[/dim]")


MAX_QUESTION_CHARS = 500     # a LoRa text is ~200; anything longer is junk
MAX_PENDING_PER_SENDER = 2   # one radio can't fill the queue for everyone else
SEEN_IDS_KEPT = 200
RECONNECT_DELAY_S = 10
# Radios on current firmware may stay silent for hours unless spoken to, so
# "deliver when heard" alone can strand mail. Also retry on a timer.
MAIL_RETRY_S = 900
# Seen 2026-11-16: the listener ran for hours hearing nothing, with no error.
# Two checks catch that: the radio library's reader thread has died (checked
# every loop), or there has been total silence for an hour (a quiet mesh is
# normal for minutes at a time, so silence alone needs a long fuse).
SILENCE_RECONNECT_S = 3600
SCAN_S = 5            # how often to look for radios plugged in or pulled out
_manager = None       # the RadioManager, once main() runs


def _reader_dead(iface: Any) -> bool:
    rx = getattr(iface, "_rxThread", None)
    return rx is not None and not rx.is_alive()

SORRY_TEXT = "[pergamon] Sorry, I couldn't answer that just now. Try again shortly."
BUSY_TEXT = "[pergamon] Still working on your earlier questions; send this again after they arrive."

# Replies run on one worker thread: the receive callback must return quickly,
# because acks for our own sends arrive on that same thread.
_jobs: "queue.Queue[tuple[Any, str, str]]" = queue.Queue()
_pending: dict[str, int] = {}
_seen: "OrderedDict[Any, None]" = OrderedDict()
_lock = threading.Lock()
_delivering: set[str] = set()
_mailbox: "mailbox.Mailbox | None" = None


def _get_mailbox() -> "mailbox.Mailbox":
    global _mailbox
    if _mailbox is None:
        _mailbox = mailbox.Mailbox()
    return _mailbox


def _node_name(interface: Any, node_id: str) -> str:
    u = ((getattr(interface, "nodes", None) or {}).get(node_id) or {}).get("user") or {}
    return u.get("longName") or u.get("shortName") or node_id


def _deliver_mail(interface: Any, recipient: str) -> None:
    """Send everything waiting for `recipient`; anything unconfirmed stays queued."""
    box = _get_mailbox()
    for m in box.pending_for(recipient):
        text = mailbox.format_delivery(m, _node_name(interface, m["sender"]))
        if not _send_confirmed(interface, recipient, text):
            console.print(f"[yellow]  mail for {recipient} not confirmed; will retry when heard again[/yellow]")
            return
        box.mark_delivered(m["id"])
        console.print(f"[green]  delivered mail #{m['id']} to {recipient}[/green]")


def _handle_feeds(sender: str, text: str) -> str | None:
    """/feeds, /sub <feed>, /unsub <feed|all>; None if it's none of those."""
    from rag import feeds
    words = text.strip().split()
    cmd = words[0].lower() if words else ""
    if cmd not in ("/feeds", "/sub", "/unsub"):
        return None
    try:
        if cmd == "/feeds" or len(words) < 2:
            mine = feeds.subscriptions(sender)
            return ("Feeds: " + ", ".join(feeds.FEEDS) + ". You get: " + (", ".join(mine) or "none")
                    + ". Send /sub news or /unsub news.")
        if cmd == "/sub":
            mine = feeds.subscribe(sender, words[1])
            return f"Subscribed. You get: {', '.join(mine)}. Digests at " + \
                ", ".join(t.strftime("%H:%M") for t in feeds.times()) + "; warnings right away."
        mine = feeds.unsubscribe(sender, words[1])
        return "Unsubscribed. You get: " + (", ".join(mine) or "nothing")
    except feeds.FeedError as e:
        return str(e)


def queue_feeds(interface: Any) -> int:
    """Send any due digests and urgent posts: radio messages to radios, sealed
    hub messages to web subscribers."""
    from rag import feeds
    sent = 0
    for who, text in feeds.plan():
        if feeds.is_radio(who):
            _jobs.put((interface, who, FEED_PREFIX + text))
        else:
            try:
                _send_hub_message(who, text)
            except Exception as e:
                console.print(f"[red]feed message to {who[:8]}… failed: {e!r}[/red]")
        sent += 1
    return sent


def _send_hub_message(pubkey: str, text: str) -> None:
    from rag import hubs, messages
    from rag.nostr import _load_relays_from_config, publish
    wrap = messages.build(hubs._nsec(), pubkey, text)[0]
    publish(wrap, _load_relays_from_config())


FEED_PREFIX = "\x00feed:"


def _handle_msg(interface: Any, sender: str, text: str) -> str | None:
    """'/msg who text' → confirmation for the sender; None if not a /msg."""
    try:
        parsed = mailbox.parse_msg(text)
        if parsed is None:
            return None
        who, body = parsed
        recipient = mailbox.resolve_recipient(who, getattr(interface, "nodes", None) or {})
        _get_mailbox().deposit(sender, recipient, body)
    except mailbox.MailboxError as e:
        return str(e)
    _queue_delivery(interface, recipient)  # they may be in range right now
    return f"Saved for {_node_name(interface, recipient)}. I'll deliver it when I hear their radio."


def _queue_delivery(interface: Any, recipient: str) -> None:
    with _lock:
        if recipient in _delivering:
            return
        _delivering.add(recipient)
    _jobs.put((interface, recipient, None))


def retry_waiting_mail(interface: Any) -> int:
    """Queue a delivery attempt for every radio with mail waiting."""
    try:
        waiting = _get_mailbox().recipients_waiting()
    except Exception as e:
        console.print(f"[red]mailbox check failed: {e!r}[/red]")
        return 0
    for r in waiting:
        _queue_delivery(interface, r)
    return len(waiting)


MORNING = "\x00morning-report"  # job marker: send the good-morning message to `sender`


def _send_morning(interface: Any, recipient: str) -> None:
    from modules.comms.meshtastic import morning
    text = morning.compose()
    console.print(f"[cyan]morning report → {recipient}[/cyan]")
    _reply(interface, recipient, MeshReply(answer=text, sources=[]))


def queue_morning_report(interface: Any, now=None) -> bool:
    """Queue today's good-morning message if it's time (once a day)."""
    from modules.comms.meshtastic import morning
    import datetime as dt
    now = now or dt.datetime.now().astimezone()
    s = morning.settings()
    if not morning.due(now, s):
        return False
    morning.take_trigger()
    morning.mark_sent(now.date())
    for r in s["to"]:
        _jobs.put((interface, r, MORNING))
    return True


STOP = object()  # put on a worker's queue to end it (tests)


def _worker(jobs: "queue.Queue | None" = None) -> None:
    """Runs every radio job, one at a time. Nothing a single job does may stop
    it: if this thread dies, the hub silently stops answering."""
    jobs = jobs or _jobs  # bound once: a worker never switches queues
    while True:
        job = jobs.get()
        if job is STOP:
            return
        try:
            _run_job(*job)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]radio job failed: {e!r}[/red]")


def _run_job(interface: Any, sender: str, text: str | None) -> None:
    if text is None:  # mail delivery job, not a question
        try:
            _deliver_mail(interface, sender)
        except Exception as e:
            console.print(f"[red]mail delivery failed: {e!r}[/red]")
        finally:
            with _lock:
                _delivering.discard(sender)
        return
    if text == MORNING:
        try:
            _send_morning(interface, sender)
        except Exception as e:
            console.print(f"[red]morning report failed: {e!r}[/red]")
        return
    if text.startswith(FEED_PREFIX):
        try:
            console.print(f"[cyan]feed → {sender}[/cyan]")
            _reply(interface, sender, MeshReply(answer=text[len(FEED_PREFIX):], sources=[]))
        except Exception as e:
            console.print(f"[red]feed delivery failed: {e!r}[/red]")
        return
    try:
        if text.strip().lower().rstrip("!.") in ("/new", "new"):
            conversations.reset(sender)
            _reply(interface, sender, MeshReply(answer="Started a new conversation.", sources=[]))
            return
        command_reply = _handle_feeds(sender, text)
        if command_reply is None:
            command_reply = _handle_msg(interface, sender, text)
        if command_reply is None:
            command_reply = commands.run(text)
        if command_reply is not None:
            _reply(interface, sender, MeshReply(answer=command_reply, sources=[]))
        else:
            reply = ask(text, history=conversations.history(sender))
            conversations.add(sender, text, reply.answer)
            _reply(interface, sender, reply)
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


_last_packet = [time.time()]
_heard: set[str] = set()


def _log_new_node(packet: dict, interface) -> None:
    """First packet from a radio this session: log who and how strong."""
    who = packet.get("fromId")
    if not who or who in _heard:
        return
    _heard.add(who)
    me = ((getattr(interface, "getMyUser", lambda: {})() or {}).get("id"))
    if who == me:
        return
    hops = None
    if packet.get("hopStart") is not None and packet.get("hopLimit") is not None:
        hops = packet["hopStart"] - packet["hopLimit"]
    console.print(f"[dim]heard {_node_name(interface, who)} {who}: snr {packet.get('rxSnr')} dB, "
                  f"{'direct' if hops == 0 else f'{hops} hops' if hops is not None else 'hops ?'}[/dim]")


def on_receive(packet: dict, interface) -> None:
    """Called by Meshtastic's pubsub for each received packet.

    Never lets an exception escape: this runs on the radio's reader thread,
    and an exception there can leave the listener running but deaf.
    """
    # Only server radios answer. A radio opened briefly to read its ID (a
    # handheld on the charger, an undecided radio) must never reply as Pergamon.
    if _manager is not None and not _manager.serves(interface):
        return
    _last_packet[0] = time.time()
    if _manager is not None:
        _manager.heard(interface)
    try:
        _log_new_node(packet, interface)
        _on_receive(packet, interface)
    except Exception:
        import traceback
        console.print(f"[red]error handling packet:[/red]\n{traceback.format_exc()}")


def _on_receive(packet: dict, interface) -> None:
    # Any packet at all (position, telemetry, text) proves a radio is in
    # range: hand over mail waiting for it.
    heard = packet.get("fromId")
    if heard:
        try:
            if _get_mailbox().has_pending(heard):
                _queue_delivery(interface, heard)
        except Exception as e:
            console.print(f"[red]mailbox check failed: {e!r}[/red]")

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
        # Public channel: log it so the host can follow the conversation, but
        # never answer (replying to broadcasts would flood the mesh).
        if text:
            console.print(f"[cyan]\\[channel {channel}] {_node_name(interface, sender)} "
                          f"({sender}): {text}[/cyan]")
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


def serial_ports() -> list[str]:
    import glob
    return sorted(glob.glob("/dev/cu.usbserial-*") + glob.glob("/dev/cu.usbmodem*")
                  + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))


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

    from modules.comms.meshtastic.manager import RadioManager
    global _manager
    _manager = RadioManager(serial_iface, serial_ports, lambda m: console.print(f"[cyan]{m}[/cyan]"),
                            silence_s=SILENCE_RECONNECT_S, fixed_port=os.environ.get("MESHTASTIC_PORT"))

    allowed = _load_allowed_nodes()
    if allowed:
        console.print(f"[dim]allowed node IDs: {sorted(allowed)}[/dim]")
    else:
        console.print("[yellow]no allowlist set — responding to all DMs[/yellow]")

    def on_lost(interface, topic=pub.AUTO_TOPIC) -> None:
        _manager.lost(interface)

    threading.Thread(target=_worker, daemon=True).start()
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_lost, "meshtastic.connection.lost")

    console.print("[green]ready. ctrl-c to quit. Watching USB for radios.[/green]")
    next_scan = 0.0
    next_mail_try = time.time() + 30
    next_morning_check = time.time() + 5
    try:
        while True:
            now = time.time()
            if now >= next_scan:
                next_scan = now + SCAN_S
                from rag import radios as _radios
                req = _radios.take_request()
                if req and req.get("preset"):
                    _manager.apply_preset(req["preset"])
                    next_scan = now + 30  # give them time to restart
                    continue
                _manager.check_health(_reader_dead)
                _manager.scan()
            radio = _manager.primary()
            if radio is not None and now >= next_morning_check:
                next_morning_check = now + 30
                for name, fn in (("morning report", queue_morning_report), ("feeds", queue_feeds)):
                    try:
                        fn(radio)
                    except Exception as e:
                        console.print(f"[red]{name} check failed: {e!r}[/red]")
            if radio is not None and now >= next_mail_try:
                next_mail_try = now + MAIL_RETRY_S
                retry_waiting_mail(radio)
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]closing[/dim]")
        _manager.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
