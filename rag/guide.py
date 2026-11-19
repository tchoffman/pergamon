"""The Local guide: what to plant now, who can help, and notes about places.

Planting times hang off the area's average last spring frost and first fall
frost. Those come from NOAA's 1991-2020 climate normals for the nearest
weather station that records them (median dates, plus the "safe" date by which
there's only a 10% chance of frost), fetched once and cached so the guide
works offline. The host can also set them by hand.

Place notes are short notes people write about local places ("the creek by
the mill has watercress in April", "Joe's garage fixes small engines"). Each
is a signed Nostr event (kind 30408), one per author per place, so editing
your note replaces it; they sync to trusted hubs and follow moderation like
everything else.

    pergamon guide frost             # show (fetching if needed) this hub's frost dates
    pergamon guide frost set 03-25 11-10
    pergamon guide now               # what to plant this week
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

import yaml

from rag.config import REPO_ROOT
from rag.nostr import Event, build_event

STATE = REPO_ROOT / "build" / "state"
FROST_PATH = STATE / "frost.json"
CROPS_PATH = REPO_ROOT / "config" / "guide" / "crops.yaml"
NOAA = "https://www.ncei.noaa.gov/data/normals-annualseasonal/1991-2020"
UA = {"User-Agent": "Pergamon-guide"}
KIND_PLACE = 30408
PLACE_TAG = "pergamon-place"
PLACE_CATEGORIES = {
    "food": "Food & foraging", "water": "Water", "repair": "Repairs & tools",
    "goods": "Shops & trading", "health": "Health & care", "nature": "Outdoors",
    "gathering": "Gathering places", "other": "Other",
}
ACTIONS = {"indoors": "Start indoors", "transplant": "Set out plants", "sow": "Sow outdoors",
           "fall_sow": "Sow for fall", "fall_transplant": "Set out for fall"}


class GuideError(ValueError):
    pass


# ───────────────────────────── frost dates ─────────────────────────────

def _miles(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _get(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def nearest_stations(lat: float, lon: float, inventory: str, n: int = 25) -> list[tuple[float, str, str]]:
    """(miles, id, name) for the closest stations in NOAA's inventory."""
    out = []
    for line in inventory.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            slat, slon = float(parts[1]), float(parts[2])
        except ValueError:
            continue
        name = " ".join(parts[5:]).split("  ")[0].strip() or parts[0]
        out.append((_miles(lat, lon, slat, slon), parts[0], re.sub(r"\s+(GSN|HCN|WMO).*$", "", name)))
    return sorted(out)[:n]


def parse_frost(csv_text: str) -> dict | None:
    """Median and 90%-safe frost dates from a station's annual normals file."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return None
    r = rows[0]
    get = lambda k: (r.get(k) or "").strip()  # noqa: E731
    last, first = get("ANN-TMIN-PRBLST-T32FP50"), get("ANN-TMIN-PRBFST-T32FP50")
    if not re.fullmatch(r"\d\d/\d\d", last) or not re.fullmatch(r"\d\d/\d\d", first):
        return None
    safe_last, safe_first = get("ANN-TMIN-PRBLST-T32FP10"), get("ANN-TMIN-PRBFST-T32FP10")
    mmdd = lambda s: s.replace("/", "-") if re.fullmatch(r"\d\d/\d\d", s) else None  # noqa: E731
    return {"last": mmdd(last), "first": mmdd(first),
            "last_safe": mmdd(safe_last), "first_safe": mmdd(safe_first)}


def fetch_frost(lat: float, lon: float, get=_get) -> dict:
    """Frost dates from the nearest NOAA station that records them."""
    inventory = get(f"{NOAA}/doc/inventory_30yr.txt")
    for miles, sid, name in nearest_stations(lat, lon, inventory):
        if miles > 150:
            break
        try:
            dates = parse_frost(get(f"{NOAA}/access/{sid}.csv", timeout=30))
        except OSError:
            continue
        if dates:
            return {**dates, "source": f"NOAA 1991-2020 normals, {name.title()} ({miles:.0f} mi)",
                    "station": sid}
    raise GuideError("No NOAA station within 150 miles records frost dates.")


def frost(fetch=True) -> dict | None:
    """This hub's frost dates: set by hand, cached, or fetched once from NOAA."""
    try:
        return json.loads(FROST_PATH.read_text())
    except (OSError, ValueError):
        pass
    if not fetch:
        return None
    from modules.comms.meshtastic.morning import coordinates, settings
    coords = coordinates(settings() or {"location": _location()})
    if not coords:
        return None
    try:
        data = fetch_frost(*coords)
    except (GuideError, OSError):
        return None
    save_frost(data)
    return data


def _location() -> str:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    return local.get("location") or ""


def save_frost(data: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    FROST_PATH.write_text(json.dumps(data, indent=1))


def set_frost(last: str, first: str) -> dict:
    for v in (last, first):
        if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", v or ""):
            raise GuideError("Dates look like 03-25 (month-day).")
    data = {"last": last, "first": first, "last_safe": None, "first_safe": None, "source": "set by the host"}
    save_frost(data)
    return data


# ───────────────────────────── planting ─────────────────────────────

def crops() -> list[dict]:
    return yaml.safe_load(CROPS_PATH.read_text())["crops"]


def _date(year: int, mmdd: str) -> dt.date:
    m, d = (int(x) for x in mmdd.split("-"))
    return dt.date(year, m, min(d, 28) if m == 2 else d)


def windows(fr: dict, year: int) -> list[dict]:
    """Every planting window this year: {crop, action, start, end}."""
    lf, ff = _date(year, fr["last"]), _date(year, fr["first"])
    out = []
    for c in crops():
        for action in ACTIONS:
            if action not in c:
                continue
            base = ff if action.startswith("fall") else lf
            a, b = c[action]
            out.append({"crop": c["name"], "action": action, "label": ACTIONS[action],
                        "start": base + dt.timedelta(weeks=a), "end": base + dt.timedelta(weeks=b),
                        "frost": c.get("frost"), "days": c.get("days")})
    return sorted(out, key=lambda w: (w["start"], w["crop"]))


def plant_now(today: dt.date | None = None, fr: dict | None = None, ahead_days: int = 14) -> dict:
    """{'now': [...], 'soon': [...], 'frost': {...}} for this week and the next two."""
    today = today or dt.date.today()
    fr = fr or frost()
    if not fr:
        return {"now": [], "soon": [], "frost": None}
    ws = windows(fr, today.year) + windows(fr, today.year + 1)
    now = [w for w in ws if w["start"] <= today <= w["end"]]
    soon = [w for w in ws if today < w["start"] <= today + dt.timedelta(days=ahead_days)]
    if not soon:  # a quiet stretch (winter): show what's next instead of nothing
        soon = [w for w in ws if w["start"] > today][:4]
    return {"now": now, "soon": soon, "frost": fr}


def describe_now(today: dt.date | None = None) -> str:
    """Plain words for the assistant or the radio. Dated and explicit, so a
    model can't read "from Jan 16" as "now"."""
    today = today or dt.date.today()
    p = plant_now(today)
    if not p["frost"]:
        return "Frost dates for this hub aren't known yet, so there's no planting calendar."
    fr = p["frost"]
    lines = [f"Today is {today:%B %-d}. Average last spring frost: {_date(today.year, fr['last']):%B %-d}; "
             f"average first fall frost: {_date(today.year, fr['first']):%B %-d} ({fr['source']})."]
    if p["now"]:
        lines.append("In its planting window this week: " + "; ".join(
            f"{w['label'].lower()} {w['crop'].lower()} (until {w['end']:%B %-d})" for w in p["now"]) + ".")
    else:
        lines.append("Nothing is in its planting window this week, so nothing should be started or "
                     "sown now.")
    by_date: dict = {}
    for w in p["soon"]:
        by_date.setdefault(w["start"], []).append(f"{w['label'].lower()} {w['crop'].lower()}")
    for start, items in sorted(by_date.items()):
        weeks = max(1, round((start - today).days / 7))
        lines.append(f"Next, starting {start:%B %-d} (in about {weeks} week{'s' if weeks != 1 else ''}): "
                     + "; ".join(items) + ".")
    return " ".join(lines)


# ───────────────────────────── place notes ─────────────────────────────

def slug(place: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (place or "").lower()).strip("-")
    if not s:
        raise GuideError("Name the place.")
    return s[:60]


def build_place_note(pubkey: str, place: str, note: str, category: str = "other",
                     location: str = "") -> Event:
    place, note = (place or "").strip(), (note or "").strip()
    if not note:
        raise GuideError("Write a note about the place.")
    if len(note) > 600 or len(place) > 80:
        raise GuideError("Keep it short: 80 characters for the place, 600 for the note.")
    if category not in PLACE_CATEGORIES:
        raise GuideError(f"Category must be one of {', '.join(PLACE_CATEGORIES)}.")
    tags = [["d", slug(place)], ["title", place], ["category", category], ["t", PLACE_TAG]]
    if location.strip():
        tags.append(["location", location.strip()[:120]])
    return build_event(kind=KIND_PLACE, content=note, pubkey_hex=pubkey, tags=tags)


def summarize_place(ev: Event) -> dict:
    tags = {t[0]: t[1] for t in ev.tags if len(t) > 1}
    return {"id": ev.id, "pubkey": ev.pubkey, "created_at": ev.created_at, "d": tags.get("d", ""),
            "place": tags.get("title", ""), "category": tags.get("category", "other"),
            "location": tags.get("location", ""), "note": ev.content}


def fetch_places(relays: list[str], query: str = "", category: str | None = None) -> list[dict]:
    """Places with every note about them, most recently noted first."""
    import asyncio
    from rag.nostr import afetch_events
    events = asyncio.run(afetch_events(relays, {"kinds": [KIND_PLACE], "#t": [PLACE_TAG], "limit": 1000}, 1000))
    notes = [summarize_place(e) for e in events]
    q = (query or "").lower().strip()
    if category:
        notes = [n for n in notes if n["category"] == category]
    if q:
        notes = [n for n in notes if q in (n["place"] + " " + n["note"] + " " + n["location"]).lower()]
    places: dict[str, dict] = {}
    for n in sorted(notes, key=lambda n: -n["created_at"]):
        p = places.setdefault(n["d"], {"d": n["d"], "place": n["place"], "category": n["category"],
                                       "location": n["location"], "notes": [], "updated": n["created_at"]})
        p["notes"].append(n)
        p["location"] = p["location"] or n["location"]
    return sorted(places.values(), key=lambda p: -p["updated"])


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "now"
    try:
        if cmd == "frost":
            if len(argv) >= 4 and argv[1] == "set":
                fr = set_frost(argv[2], argv[3])
            else:
                fr = frost()
            if not fr:
                print("Frost dates unknown: set the hub's location, or run "
                      "`pergamon guide frost set 03-25 11-10`.", file=sys.stderr)
                return 1
            print(f"Last frost {fr['last']} (safe after {fr.get('last_safe') or '?'}), "
                  f"first frost {fr['first']} (safe until {fr.get('first_safe') or '?'}). {fr['source']}.")
            return 0
        if cmd == "now":
            print(describe_now())
            return 0
    except GuideError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
