from modules.comms.meshtastic import listen
from modules.comms.meshtastic.common import MeshReply


class FakeIface:
    """Acks each send with the next scripted result (None = never answers)."""

    def __init__(self, results):
        self.results = list(results)
        self.sent = []

    def sendText(self, text, destinationId, wantAck=False, onResponse=None):
        self.sent.append(text)
        r = self.results.pop(0) if self.results else "NONE"
        if r is not None:
            onResponse({"decoded": {"routing": {"errorReason": r}}})


def test_sends_every_part_when_acked():
    iface = FakeIface([])
    listen._reply(iface, "!b", MeshReply(answer="word " * 120, sources=[]))
    assert len(iface.sent) > 1 and iface.sent[-1].startswith(f"[{len(iface.sent)}/")


def test_retries_a_part_until_acked(monkeypatch):
    monkeypatch.setattr(listen, "ACK_TIMEOUT_S", 0.01)
    iface = FakeIface([None, "MAX_RETRANSMIT", "NONE"])
    assert listen._send_confirmed(iface, "!b", "hi")
    assert iface.sent == ["hi", "hi", "hi"]


def test_stops_after_a_part_fails(monkeypatch):
    monkeypatch.setattr(listen, "ACK_TIMEOUT_S", 0.01)
    iface = FakeIface([None] * 10)
    listen._reply(iface, "!b", MeshReply(answer="word " * 120, sources=[]))
    assert iface.sent and all(s.startswith("[1/") for s in iface.sent)
    assert len(iface.sent) == listen.ATTEMPTS


def _dm(text, pid, sender="!b"):
    return {"decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text},
            "fromId": sender, "from": 1, "toId": "!a", "id": pid}


class RecordingIface:
    def __init__(self):
        self.sent = []

    def sendText(self, text, destinationId, **kw):
        self.sent.append(text)


def _reset(monkeypatch):
    import queue
    monkeypatch.setattr(listen, "_jobs", queue.Queue())
    monkeypatch.setattr(listen, "_pending", {})
    monkeypatch.setattr(listen, "_seen", listen.OrderedDict())
    monkeypatch.setattr(listen, "_load_allowed_nodes", lambda: set())


def test_duplicate_packet_answered_once(monkeypatch):
    _reset(monkeypatch)
    iface = RecordingIface()
    listen.on_receive(_dm("hi", 7), iface)
    listen.on_receive(_dm("hi", 7), iface)
    assert listen._jobs.qsize() == 1


def test_flooding_sender_is_told_to_wait(monkeypatch):
    _reset(monkeypatch)
    iface = RecordingIface()
    for pid in range(listen.MAX_PENDING_PER_SENDER + 1):
        listen.on_receive(_dm(f"q{pid}", pid), iface)
    assert listen._jobs.qsize() == listen.MAX_PENDING_PER_SENDER
    assert iface.sent == [listen.BUSY_TEXT]


def test_long_question_is_truncated(monkeypatch):
    _reset(monkeypatch)
    listen.on_receive(_dm("x" * 5000, 1), RecordingIface())
    assert len(listen._jobs.get_nowait()[2]) == listen.MAX_QUESTION_CHARS


def test_commands_skip_the_model(monkeypatch):
    _reset(monkeypatch)
    def no_model(text):
        raise AssertionError("model should not run for ping")
    monkeypatch.setattr(listen, "ask", no_model)
    iface = FakeIface([])
    listen._jobs.put((iface, "!b", "Ping"))
    listen._pending["!b"] = 1
    listen.threading.Thread(target=listen._worker, daemon=True).start()
    for _ in range(100):
        if iface.sent:
            break
        listen.time.sleep(0.01)
    assert iface.sent == ["[1/1] pong"]


def test_errors_send_a_plain_apology_not_internals(monkeypatch):
    _reset(monkeypatch)
    def boom(text):
        raise RuntimeError("connection refused http://localhost:11434 /Users/x/secret")
    monkeypatch.setattr(listen, "ask", boom)
    iface = FakeIface([])
    listen._jobs.put((iface, "!b", "question"))
    listen._pending["!b"] = 1
    t = listen.threading.Thread(target=listen._worker, daemon=True)
    t.start()
    for _ in range(100):
        if iface.sent:
            break
        listen.time.sleep(0.01)
    assert iface.sent == [listen.SORRY_TEXT]


def test_handler_errors_never_escape_to_the_reader_thread(monkeypatch):
    def boom(packet, interface):
        raise RuntimeError("bad packet")
    monkeypatch.setattr(listen, "_on_receive", boom)
    listen.on_receive({"fromId": "!b"}, RecordingIface())  # must not raise


def test_every_packet_updates_last_heard(monkeypatch):
    _reset(monkeypatch)
    listen._last_packet[0] = 0
    listen.on_receive({"fromId": "!x", "decoded": {"portnum": "TELEMETRY_APP"}}, RecordingIface())
    assert listen._last_packet[0] > 0


def test_public_channel_is_logged_not_answered(monkeypatch, capsys):
    _reset(monkeypatch)
    iface = RecordingIface()
    pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello mesh"},
           "fromId": "!c", "from": 3, "toId": "^all", "id": 99}
    listen.on_receive(pkt, iface)
    assert listen._jobs.empty() and iface.sent == []
    assert "hello mesh" in capsys.readouterr().out


def test_reader_dead_detection():
    import threading as th

    class I:
        pass
    i = I()
    t = th.Thread(target=lambda: None)
    t.start(); t.join()
    i._rxThread = t
    assert listen._reader_dead(i)
    alive = th.Event()
    t2 = th.Thread(target=alive.wait, daemon=True)
    t2.start()
    i._rxThread = t2
    assert not listen._reader_dead(i)
    alive.set()
    assert not listen._reader_dead(I())  # no thread attribute: can't tell, assume fine


def test_log_file_gets_timestamped_plain_copy(tmp_path):
    c = listen._TeeConsole(tmp_path / "l.log")
    c.print("[bold]!b[/bold] (ch 0): ping")
    line = (tmp_path / "l.log").read_text()
    assert "!b (ch 0): ping" in line and "[bold]" not in line and line[:4].isdigit()
