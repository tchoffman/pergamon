import pytest

from modules.comms.meshtastic import listen, mailbox
from modules.comms.meshtastic.mailbox import Mailbox, MailboxError, parse_msg, resolve_recipient

NODES = {
    "!aaaaaaaa": {"user": {"id": "!aaaaaaaa", "longName": "Ada Base", "shortName": "ADA"}},
    "!bbbbbbbb": {"user": {"id": "!bbbbbbbb", "longName": "Bob Handheld", "shortName": "BOB"}},
    "!cccccccc": {"user": {"id": "!cccccccc", "longName": "Twin", "shortName": "T1"}},
    "!dddddddd": {"user": {"id": "!dddddddd", "longName": "Twin", "shortName": "T2"}},
}


@pytest.fixture
def box(tmp_path):
    return Mailbox(tmp_path / "mail.db")


def test_deposit_and_deliver_once(box):
    box.deposit("!aaaaaaaa", "!bbbbbbbb", "meet at the church 3pm")
    [m] = box.pending_for("!bbbbbbbb")
    assert m["text"] == "meet at the church 3pm"
    box.mark_delivered(m["id"])
    assert box.pending_for("!bbbbbbbb") == [] and not box.has_pending("!bbbbbbbb")


def test_limits(box):
    for i in range(mailbox.MAX_PENDING_FOR_RECIPIENT):
        box.deposit(f"!s{i:07d}", "!bbbbbbbb", "hi")
    with pytest.raises(MailboxError, match="mailbox is full"):
        box.deposit("!aaaaaaaa", "!bbbbbbbb", "hi")
    with pytest.raises(MailboxError, match="own radio"):
        box.deposit("!aaaaaaaa", "!aaaaaaaa", "hi")
    with pytest.raises(MailboxError, match="empty"):
        box.deposit("!aaaaaaaa", "!cccccccc", "   ")


def test_text_is_capped(box):
    box.deposit("!aaaaaaaa", "!bbbbbbbb", "x" * 1000)
    assert len(box.pending_for("!bbbbbbbb")[0]["text"]) == mailbox.MAX_TEXT


def test_mail_expires(box):
    box.deposit("!aaaaaaaa", "!bbbbbbbb", "old", now=1_000_000)
    assert box.pending_for("!bbbbbbbb", now=1_000_000 + 8 * 86400) == []


def test_resolve_by_id_short_and_long_name():
    assert resolve_recipient("!BBBBBBBB", NODES) == "!bbbbbbbb"
    assert resolve_recipient("bob", NODES) == "!bbbbbbbb"
    assert resolve_recipient("Ada Base", NODES) == "!aaaaaaaa"
    with pytest.raises(MailboxError, match="Several"):
        resolve_recipient("twin", NODES)
    with pytest.raises(MailboxError, match="Don't know"):
        resolve_recipient("zed", NODES)


def test_parse_msg():
    assert parse_msg("/msg bob meet at 3") == ("bob", "meet at 3")
    assert parse_msg("how do I purify water") is None
    with pytest.raises(MailboxError, match="Usage"):
        parse_msg("/msg bob")


class Radio:
    """Fake interface: acks every send, records them, has a node list."""
    nodes = NODES

    def __init__(self):
        self.sent = []

    def sendText(self, text, destinationId, wantAck=False, onResponse=None):
        self.sent.append((destinationId, text))
        if onResponse:
            onResponse({"decoded": {"routing": {"errorReason": "NONE"}}})


def test_listener_holds_mail_until_recipient_is_heard(box, monkeypatch):
    import queue
    monkeypatch.setattr(listen, "_mailbox", box)
    monkeypatch.setattr(listen, "_jobs", queue.Queue())
    monkeypatch.setattr(listen, "_delivering", set())
    radio = Radio()

    reply = listen._handle_msg(radio, "!aaaaaaaa", "/msg bob meet at the church 3pm")
    assert reply.startswith("Saved for Bob Handheld")
    # deposit queued an immediate delivery attempt; simulate Bob out of range
    listen._jobs.get_nowait()
    listen._delivering.clear()
    assert box.has_pending("!bbbbbbbb")

    # Later Bob's radio sends a position packet: mail goes out
    listen.on_receive({"fromId": "!bbbbbbbb", "decoded": {"portnum": "POSITION_APP"}}, radio)
    iface, who, text = listen._jobs.get_nowait()
    assert who == "!bbbbbbbb" and text is None
    listen._deliver_mail(radio, "!bbbbbbbb")
    assert radio.sent == [("!bbbbbbbb", radio.sent[0][1])]
    assert "Mail from Ada Base" in radio.sent[0][1] and "church 3pm" in radio.sent[0][1]
    assert not box.has_pending("!bbbbbbbb")


def test_unknown_recipient_explained(box, monkeypatch):
    monkeypatch.setattr(listen, "_mailbox", box)
    assert "Don't know a radio called 'zed'" in listen._handle_msg(Radio(), "!aaaaaaaa", "/msg zed hi")
