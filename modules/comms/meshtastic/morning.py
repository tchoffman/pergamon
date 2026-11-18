"""A good-morning radio message with the day's weather, sent by the listener.

    # config/install.local.yaml
    comms:
      meshtastic:
        morning_report:
          time: "07:00"            # local time; sent once a day
          to: ["!e06fbbd0"]        # default: the allowlist
          lat: 33.95               # default: looked up once from `location`
          lon: -84.55

The forecast comes from the US National Weather Service while the internet is
up. When it isn't, the message says so plainly and still gives sunrise and
sunset, which are worked out on this machine.

    pergamon mesh morning              # show today's message (sends nothing)
    pergamon mesh morning --send-now   # ask the running listener to send it now
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from rag.config import REPO_ROOT

STATE_DIR = REPO_ROOT / "build" / "state"
STATE = STATE_DIR / "morning_report.json"
TRIGGER = STATE_DIR / "morning_report.now"
LATE_LIMIT = dt.timedelta(hours=3)  # listener was down at send time: still send up to 3h late
UA = {"User-Agent": "Pergamon-weather"}


# ───────────────────────────── config ─────────────────────────────

def settings() -> dict | None:
    """The morning_report block, or None when it's turned off."""
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    mesh = ((local.get("comms") or {}).get("meshtastic") or {})
    from rag import feeds
    rep = mesh.get("morning_report")
    subscribed = feeds.weather_subscribers()  # radios that sent /sub weather
    if not rep and not subscribed:
        return None
    rep = rep or {}
    to = (rep.get("to") or mesh.get("allowed_node_ids") or []) if rep else []
    to = list(dict.fromkeys(list(to) + subscribed))
    hh, mm = (int(x) for x in str(rep.get("time", "07:00")).split(":"))
    return {"time": dt.time(hh, mm), "to": list(to), "lat": rep.get("lat"), "lon": rep.get("lon"),
            "location": local.get("location") or ""}


def coordinates(s: dict) -> tuple[float, float] | None:
    if s.get("lat") is not None and s.get("lon") is not None:
        return float(s["lat"]), float(s["lon"])
    cache = STATE_DIR / "location.json"
    try:
        c = json.loads(cache.read_text())
        if c.get("location") == s["location"]:
            return c["lat"], c["lon"]
    except (OSError, ValueError, KeyError):
        pass
    if not s["location"]:
        return None
    try:
        q = urllib.parse.urlencode({"q": s["location"], "format": "json", "limit": 1})
        req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            hit = json.load(r)[0]
    except (OSError, ValueError, IndexError):
        return None
    lat, lon = float(hit["lat"]), float(hit["lon"])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"location": s["location"], "lat": lat, "lon": lon}))
    return lat, lon


# ─────────────────────────── sunrise / sunset ───────────────────────────

def sun_times(day: dt.date, lat: float, lon: float) -> tuple[dt.datetime, dt.datetime] | None:
    """Local sunrise and sunset (NOAA's simplified method; within a minute or two)."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if not -1 <= cos_ha <= 1:
        return None  # midnight sun or polar night
    ha = math.degrees(math.acos(cos_ha))
    midnight = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    rise = midnight + dt.timedelta(minutes=720 - 4 * (lon + ha) - eqtime)
    sset = midnight + dt.timedelta(minutes=720 - 4 * (lon - ha) - eqtime)
    return rise.astimezone(), sset.astimezone()


def _clock(t: dt.datetime) -> str:
    return t.strftime("%-I:%M")


# ───────────────────────────── forecast ─────────────────────────────

def nws_periods(lat: float, lon: float) -> list[dict]:
    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return json.load(r)
    point = get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    return get(point["properties"]["forecast"])["properties"]["periods"]


def _period(p: dict) -> str:
    rain = (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
    temp = f"{'low' if not p.get('isDaytime', True) else 'high'} {p['temperature']}"
    text = f"{p['name']}: {p['shortForecast'].lower()}, {temp}"
    return text + (f", rain {rain}%" if rain >= 20 else "")


def compose(now: dt.datetime | None = None, periods=None, coords=None, fetch=nws_periods) -> str:
    """Today's message. `periods` / `coords` let tests skip the network."""
    now = now or dt.datetime.now().astimezone()
    s = settings() or {"location": ""}
    coords = coords if coords is not None else coordinates(s)
    parts = ["Good morning!"]
    if coords and periods is None:
        try:
            periods = fetch(*coords)
        except Exception:  # noqa: BLE001 — offline is the expected failure
            periods = None
    if periods:
        parts.append(". ".join(_period(p) for p in periods[:3]) + ".")
    else:
        parts.append("No forecast today: the weather service can't be reached.")
    if coords:
        st = sun_times(now.date(), *coords)
        if st:
            parts.append(f"Sunrise {_clock(st[0])}, sunset {_clock(st[1])}.")
    return " ".join(parts)


# ───────────────────────────── schedule ─────────────────────────────

def _last_sent() -> str | None:
    try:
        return json.loads(STATE.read_text()).get("date")
    except (OSError, ValueError):
        return None


def mark_sent(day: dt.date) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"date": day.isoformat()}))


def due(now: dt.datetime, s: dict | None, last_sent: str | None = None) -> bool:
    """True once a day, from the set time until LATE_LIMIT after it, or when asked."""
    if not s or not s["to"]:
        return False
    if TRIGGER.exists():
        return True
    at = now.replace(hour=s["time"].hour, minute=s["time"].minute, second=0, microsecond=0)
    sent = last_sent if last_sent is not None else _last_sent()
    return sent != now.date().isoformat() and at <= now < at + LATE_LIMIT


def take_trigger() -> None:
    TRIGGER.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if "--send-now" in argv:
        if not settings():
            print("Morning report is off. Add comms.meshtastic.morning_report to "
                  "config/install.local.yaml (see `pergamon mesh morning --help`).", file=sys.stderr)
            return 1
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        TRIGGER.touch()
        print("Asked the radio listener to send this morning's report now "
              "(it checks every few seconds; is `pergamon mesh listen` running?).")
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    s = settings()
    print(compose())
    print(f"\n({'sent at ' + s['time'].strftime('%H:%M') + ' to ' + ', '.join(s['to']) if s else 'not scheduled: morning_report is off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
