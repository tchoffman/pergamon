"""Feeds: subscriptions, digests with only what's new, urgent warnings."""
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from rag import board, feeds, moderation, nostr, web
from rag.identity import generate_keypair
from rag.nostr import Event, sign_event
from tests.mini_relay import MiniRelay

ET = dt.timezone(dt.timedelta(hours=-5))


@pytest.fixture
def relay(tmp_path, monkeypatch):
    r = MiniRelay().start()
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([r.url])
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    yield r
    r.stop()


def post(relay, text, category="news", keys=None):
    nsec, _, pub = keys or generate_keypair()
    ev = sign_event(board.build_post(pub, text, category), nsec)
    nostr.publish(ev, [relay.url])
    return ev


def test_feed_names_are_forgiving():
    assert [feeds.feed_id(w) for w in ("News", "warning", "lost", "events", "announcements")] == \
        ["news", "warnings", "lost-found", "events", "notices"]
    with pytest.raises(feeds.FeedError, match="No feed called"):
        feeds.feed_id("gossip")


def test_subscribe_and_unsubscribe():
    assert feeds.subscribe("!aaaa", "news") == ["news"]
    assert feeds.subscribe("!aaaa", "warnings") == ["news", "warnings"]
    assert feeds.subscribers("news") == ["!aaaa"]
    assert feeds.unsubscribe("!aaaa", "news") == ["warnings"]
    assert feeds.unsubscribe("!aaaa", "all") == [] and feeds.subscribers("warnings") == []


def test_digest_fits_two_radio_messages():
    posts = [{"text": f"Item number {i}: " + "detail " * 20, "location": ""} for i in range(8)]
    text = feeds.compose("news", posts)
    assert text.startswith("Local news: ") and len(text.encode()) <= feeds.DIGEST_BYTES
    assert "more on the board)" in text
    assert feeds.compose("news", posts[:1]).count("Item") == 1


def _at(hh, mm=0):
    return dt.datetime.now(ET).replace(hour=hh, minute=mm, second=0, microsecond=0)


def test_digests_carry_only_new_posts_at_set_times(relay):
    feeds.subscribe("!aaaa", "news")
    feeds.plan(_at(6, 0))                       # first look: sets the starting point
    time.sleep(1.1)
    post(relay, "Road closed on Elm St")
    assert feeds.plan(_at(6, 30)) == []         # not a digest time
    first = feeds.plan(_at(7, 1))
    assert first == [("!aaaa", "Local news: Road closed on Elm St")]
    assert feeds.plan(_at(7, 30)) == []         # this slot already went out
    time.sleep(1.1)
    post(relay, "Market Saturday at the church")
    noon = feeds.plan(_at(12, 5))
    assert noon == [("!aaaa", "Local news: Market Saturday at the church")]


def test_warnings_go_out_right_away_and_hidden_posts_never(relay):
    feeds.subscribe("!aaaa", "warnings")
    feeds.subscribe("!bbbb", "warnings")
    feeds.plan(_at(9, 0))
    time.sleep(1.1)
    hidden = post(relay, "fake warning spam", "warning")
    moderation.hide(hidden.id)
    post(relay, "Boil water notice for Maple Ave", "warning")
    out = feeds.plan(_at(9, 3))                 # not a digest time: urgent only
    assert sorted(out) == [("!aaaa", "Warnings: Boil water notice for Maple Ave"),
                           ("!bbbb", "Warnings: Boil water notice for Maple Ave")]
    assert feeds.plan(_at(9, 4)) == []          # each post once


def test_radio_commands():
    from modules.comms.meshtastic.listen import _handle_feeds
    assert "Feeds: weather, news" in _handle_feeds("!aaaa", "/feeds")
    assert "You get: news" in _handle_feeds("!aaaa", "/sub news")
    assert "No feed called" in _handle_feeds("!aaaa", "/sub gossip")
    assert "You get: nothing" in _handle_feeds("!aaaa", "/unsub news")
    assert _handle_feeds("!aaaa", "how do I bake bread") is None


def test_listener_sends_radio_and_web_subscribers(relay, monkeypatch):
    from modules.comms.meshtastic import listen
    from rag import messages
    w_nsec, _, w_pub = generate_keypair()
    feeds.subscribe("!aaaa", "warnings")
    feeds.subscribe(w_pub, "warnings")
    feeds.plan(_at(9, 0))
    time.sleep(1.1)
    post(relay, "Downed power line on Oak Rd", "warning")
    queued = []
    monkeypatch.setattr(listen._jobs, "put", queued.append)
    real = feeds.plan
    monkeypatch.setattr(feeds, "plan", lambda now=None, relays=None: real(_at(9, 5)))
    assert listen.queue_feeds("iface") == 2
    assert queued == [("iface", "!aaaa", listen.FEED_PREFIX + "Warnings: Downed power line on Oak Rd")]
    wraps = [e for e in relay.events.values() if e["kind"] == 1059]
    opened = messages.open_wrap(w_nsec, messages._event(wraps[0]))
    from rag import hubs
    assert opened["text"] == "Warnings: Downed power line on Oak Rd" and opened["from"] == hubs.pubkey()


def _proof(nsec, pub, purpose="feeds"):
    ev = Event(kind=27235, pubkey=pub, tags=[["purpose", purpose]], content="x")
    return sign_event(ev, nsec).to_json()


def test_web_subscriptions_need_the_owners_signature():
    nsec, _, pub = generate_keypair()
    c = TestClient(web.app)
    assert [f["id"] for f in c.get("/api/feeds").json()["feeds"]][:2] == ["weather", "news"]
    r = c.post("/api/feeds/mine", json={"proof": _proof(nsec, pub), "feeds": ["news", "warnings"]})
    assert r.json() == {"feeds": ["news", "warnings"]} and feeds.subscriptions(pub) == ["news", "warnings"]
    assert c.post("/api/feeds/mine", json={"proof": _proof(nsec, pub)}).json() == {"feeds": ["news", "warnings"]}
    assert c.post("/api/feeds/mine", json={"proof": _proof(nsec, pub, "inbox"), "feeds": []}).status_code == 401
    assert c.post("/api/feeds/mine", json={"proof": _proof(nsec, pub), "feeds": ["gossip"]}).status_code == 400


def test_weather_subscribers_get_the_morning_report(tmp_path, monkeypatch):
    from modules.comms.meshtastic import morning
    from rag import install_config
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", tmp_path / "none.yaml")
    assert morning.settings() is None
    feeds.subscribe("!cccc", "weather")
    assert morning.settings()["to"] == ["!cccc"]
