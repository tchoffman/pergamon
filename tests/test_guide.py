"""Local guide: frost dates, planting windows, place notes."""
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from rag import guide, moderation, nostr, web
from rag.identity import generate_keypair
from rag.nostr import sign_event
from tests.mini_relay import MiniRelay

MARIETTA = {"last": "03-27", "first": "11-07", "last_safe": "04-12", "first_safe": "10-27",
            "source": "NOAA test"}

HEADER = ('"STATION","NAME","ANN-TMIN-PRBLST-T32FP50","ANN-TMIN-PRBFST-T32FP50",'
          '"ANN-TMIN-PRBLST-T32FP10","ANN-TMIN-PRBFST-T32FP10"')


def test_parse_noaa_frost_columns():
    good = HEADER + '\n"USC1","MABLETON","   03/27","   11/07","   04/12","   10/27"\n'
    assert guide.parse_frost(good) == {"last": "03-27", "first": "11-07",
                                       "last_safe": "04-12", "first_safe": "10-27"}
    rain_only = HEADER + '\n"US1GA","KENNESAW","","","",""\n'
    assert guide.parse_frost(rain_only) is None


def test_nearest_station_that_records_frost():
    inventory = ("US1GACB0006  33.9566  -84.6717  318.2 GA KENNESAW 5.6 SW\n"
                 "USC00095443  33.8300  -84.5800  290.0 GA MABLETON 1 N\n"
                 "USW00013874  33.6300  -84.4400  308.0 GA ATLANTA HARTSFIELD INTL AP   GSN 72219\n")
    files = {"US1GACB0006": HEADER + '\n"US1GACB0006","K","","","",""\n',
             "USC00095443": HEADER + '\n"USC00095443","M","03/27","11/07","04/12","10/27"\n'}

    def fake_get(url, timeout=60):
        if url.endswith("inventory_30yr.txt"):
            return inventory
        return files[url.rsplit("/", 1)[1][:-4]]
    got = guide._real_fetch_frost(33.95, -84.55, get=fake_get)  # the real lookup, fake network
    assert got["last"] == "03-27" and got["station"] == "USC00095443" and "Mableton" in got["source"]


def test_planting_windows_follow_the_frost_dates():
    spring = guide.plant_now(dt.date(2026, 4, 10), MARIETTA)
    now = {(w["crop"], w["action"]) for w in spring["now"]}
    assert ("Tomatoes", "transplant") in now and ("Peas", "sow") not in now
    early = guide.plant_now(dt.date(2026, 2, 20), MARIETTA)
    assert ("Peas", "sow") in {(w["crop"], w["action"]) for w in early["now"]}
    fall = guide.plant_now(dt.date(2026, 8, 20), MARIETTA)
    assert ("Collards", "fall_transplant") in {(w["crop"], w["action"]) for w in fall["now"]}


def test_winter_shows_whats_next_instead_of_nothing():
    p = guide.plant_now(dt.date(2026, 11, 18), MARIETTA)
    assert p["now"] == [] and p["soon"] and p["soon"][0]["start"] == dt.date(2027, 1, 16)


def test_set_frost_by_hand_and_validation():
    assert guide.frost(fetch=False) is None
    guide.set_frost("03-25", "11-10")
    assert guide.frost()["source"] == "set by the host"
    with pytest.raises(guide.GuideError):
        guide.set_frost("March 25", "11-10")


def test_assistant_tool_describes_the_season():
    from rag import agent
    assert "aren't known" in agent.planting_calendar()
    guide.save_frost(MARIETTA)
    text = agent.planting_calendar()
    assert "Average last frost 03-27" in text and "NOAA test" in text


@pytest.fixture
def relay(tmp_path, monkeypatch):
    r = MiniRelay().start()
    monkeypatch.setattr(nostr, "INSTALL_LOCAL_PATH", tmp_path / "install.local.yaml")
    nostr._save_relays([r.url])
    monkeypatch.setattr(moderation, "PATH", tmp_path / "moderation.json")
    yield r
    r.stop()


def note(relay, keys, place, text, category="food", location=""):
    nsec, _, pub = keys
    ev = sign_event(guide.build_place_note(pub, place, text, category, location), nsec)
    assert nostr.publish(ev, [relay.url])[relay.url][0]
    return ev


def test_place_notes_group_by_place_and_edits_replace(relay):
    rosa, ben = generate_keypair(), generate_keypair()
    note(relay, rosa, "Mill Creek", "Watercress in April", location="behind the library")
    note(relay, ben, "mill creek!", "Clear water above the bridge")
    note(relay, ben, "Joe's Garage", "Fixes small engines", category="repair")
    places = guide.fetch_places([relay.url])
    mill = next(p for p in places if p["d"] == "mill-creek")
    assert len(mill["notes"]) == 2 and mill["location"] == "behind the library"
    time.sleep(1.1)
    note(relay, rosa, "Mill Creek", "Watercress in April and May")  # Rosa edits hers
    mill = next(p for p in guide.fetch_places([relay.url]) if p["d"] == "mill-creek")
    assert sorted(n["note"] for n in mill["notes"]) == ["Clear water above the bridge", "Watercress in April and May"]
    assert [p["place"] for p in guide.fetch_places([relay.url], category="repair")] == ["Joe's Garage"]
    assert [p["d"] for p in guide.fetch_places([relay.url], query="engines")] == ["joe-s-garage"]


def test_hidden_place_notes_disappear(relay):
    ev = note(relay, generate_keypair(), "Fake shop", "Send money first")
    moderation.hide(ev.id, kind=30408, pubkey=ev.pubkey, tags=ev.tags)
    assert guide.fetch_places([relay.url]) == []


def test_place_note_validation():
    _, _, pub = generate_keypair()
    for args in (("", "x"), ("Mill", ""), ("Mill", "x" * 601)):
        with pytest.raises(guide.GuideError):
            guide.build_place_note(pub, *args)
    with pytest.raises(guide.GuideError, match="Category"):
        guide.build_place_note(pub, "Mill", "ok", "nightlife")


def test_web_endpoints(relay, monkeypatch):
    c = TestClient(web.app)
    assert c.get("/api/guide/planting").json()["frost"] is None
    assert c.post("/api/guide/frost", json={"last": "03-27", "first": "11-07"}).status_code == 200
    d = c.get("/api/guide/planting").json()
    assert d["frost"]["last"] == "03-27" and d["year"]
    nsec, _, pub = generate_keypair()
    draft = c.post("/api/draft/place", json={"pubkey": pub, "place": "Mill Creek", "note": "Watercress",
                                             "category": "food"}).json()["event"]
    signed = sign_event(nostr.Event(**{k: draft[k] for k in ("kind", "content", "pubkey", "tags", "created_at")}), nsec)
    assert c.post("/api/publish", json={"event": signed.to_json()}).status_code == 200
    places = c.get("/api/guide/places").json()["places"]
    assert places[0]["place"] == "Mill Creek" and places[0]["notes"][0]["note"] == "Watercress"
    monkeypatch.setattr(web, "_is_host", lambda request: False)
    assert c.post("/api/guide/frost", json={"last": "01-01", "first": "12-01"}).status_code == 403
