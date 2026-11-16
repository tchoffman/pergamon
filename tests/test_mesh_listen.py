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
