import datetime as dt

import pytest
import yaml

from modules.comms.meshtastic import morning
from rag import install_config

ET = dt.timezone(dt.timedelta(hours=-5))
PERIODS = [
    {"name": "Today", "shortForecast": "Sunny", "temperature": 61, "isDaytime": True,
     "probabilityOfPrecipitation": {"value": 0}},
    {"name": "Tonight", "shortForecast": "Chance Showers", "temperature": 43, "isDaytime": False,
     "probabilityOfPrecipitation": {"value": 40}},
    {"name": "Wednesday", "shortForecast": "Sunny", "temperature": 64, "isDaytime": True},
]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "install.local.yaml"
    monkeypatch.setattr(install_config, "INSTALL_LOCAL_PATH", path)
    monkeypatch.setattr(morning, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(morning, "STATE", tmp_path / "state" / "m.json")
    monkeypatch.setattr(morning, "TRIGGER", tmp_path / "state" / "now")

    def write(report):
        path.write_text(yaml.safe_dump({"comms": {"meshtastic": {
            "allowed_node_ids": ["!aaaa"], "morning_report": report}}}))
    return write


def test_message_with_forecast_fits_one_radio_packet(cfg):
    cfg({"time": "07:00", "lat": 33.95, "lon": -84.55})
    msg = morning.compose(dt.datetime(2026, 11, 17, 7, tzinfo=ET), periods=PERIODS, coords=(33.95, -84.55))
    assert msg.startswith("Good morning! Today: sunny, high 61. Tonight: chance showers, low 43, rain 40%.")
    assert "Sunrise" in msg and len(msg.encode()) <= 200


def test_offline_says_so_and_still_gives_sun_times(cfg):
    cfg({"time": "07:00"})
    def no_internet(lat, lon):
        raise OSError("network unreachable")
    msg = morning.compose(dt.datetime(2026, 11, 17, 7, tzinfo=ET), coords=(33.95, -84.55), fetch=no_internet)
    assert "can't be reached" in msg and "Sunrise" in msg


def test_sun_times_for_marietta_in_november():
    rise, sset = morning.sun_times(dt.date(2026, 11, 17), 33.9526, -84.5499)
    rise, sset = rise.astimezone(ET), sset.astimezone(ET)
    assert (rise.hour, sset.hour) == (7, 17)  # about 7:12 am and 5:34 pm EST
    assert 10 * 60 < (sset - rise).total_seconds() / 60 < 10.5 * 60


def test_due_once_a_day_and_up_to_three_hours_late(cfg):
    cfg({"time": "07:00"})
    s = morning.settings()
    assert s["to"] == ["!aaaa"]  # defaults to the allowlist
    day = dt.datetime(2026, 11, 17, tzinfo=ET)
    assert not morning.due(day.replace(hour=6, minute=59), s, last_sent="")
    assert morning.due(day.replace(hour=7), s, last_sent="")
    assert morning.due(day.replace(hour=9, minute=30), s, last_sent="")  # listener was down at 7
    assert not morning.due(day.replace(hour=10, minute=1), s, last_sent="")
    assert not morning.due(day.replace(hour=7, minute=5), s, last_sent="2026-11-17")


def test_send_now_trigger_overrides_schedule(cfg):
    cfg({"time": "07:00"})
    assert morning.main(["--send-now"]) == 0
    s = morning.settings()
    assert morning.due(dt.datetime(2026, 11, 17, 15, tzinfo=ET), s, last_sent="2026-11-17")
    morning.take_trigger()
    assert not morning.due(dt.datetime(2026, 11, 17, 15, tzinfo=ET), s, last_sent="2026-11-17")


def test_off_unless_configured(cfg, tmp_path):
    install_config.INSTALL_LOCAL_PATH.write_text("comms: {}\n")
    assert morning.settings() is None
    assert not morning.due(dt.datetime(2026, 11, 17, 7, tzinfo=ET), None)


def test_listener_queues_one_report_per_recipient_once(cfg, monkeypatch):
    from modules.comms.meshtastic import listen
    cfg({"time": "07:00", "to": ["!aaaa", "!bbbb"]})
    queued = []
    monkeypatch.setattr(listen._jobs, "put", queued.append)
    at7 = dt.datetime(2026, 11, 17, 7, 1, tzinfo=ET)
    assert listen.queue_morning_report("iface", now=at7)
    assert [(r, t) for _, r, t in queued] == [("!aaaa", listen.MORNING), ("!bbbb", listen.MORNING)]
    assert not listen.queue_morning_report("iface", now=at7.replace(minute=30))  # already sent today
