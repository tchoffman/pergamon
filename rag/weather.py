"""Live weather when the hub has internet (US National Weather Service).

Pergamon works without the internet, but uses it when it's there. This tool
gives the assistant (web and radio) the forecast for the hub's location or a
named US place; offline, it says so, and the assistant falls back to what's
typical for the season.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "Pergamon-weather"}


def _hub_coords() -> tuple[float, float] | None:
    from modules.comms.meshtastic import morning
    s = morning.settings() or {"location": morning_location()}
    return morning.coordinates(s)


def morning_location() -> str:
    from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
    local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
    return local.get("location") or ""


def _geocode(place: str) -> tuple[float, float] | None:
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1, "countrycodes": "us"})
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        hits = json.load(r)
    return (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None


def forecast(place: str = "", periods=None, geocode=_geocode, fetch=None) -> str:
    """Text for the assistant: the next few forecast periods, or why not."""
    from modules.comms.meshtastic.morning import nws_periods
    fetch = fetch or nws_periods
    try:
        coords = geocode(place) if place.strip() else _hub_coords()
    except OSError:
        return "No internet connection right now, so no live forecast is available."
    if not coords:
        return (f"Couldn't find '{place}'. Live forecasts cover US places only."
                if place.strip() else "This hub's location isn't set, so there's no forecast.")
    if periods is None:
        try:
            periods = fetch(*coords)
        except Exception:  # noqa: BLE001 — offline, or outside the US
            return ("No live forecast available right now (no internet, or the place is outside "
                    "the US National Weather Service's area).")
    where = place.strip() or morning_location() or "this hub's area"
    lines = [f"Live forecast for {where} (US National Weather Service):"]
    for p in periods[:4]:
        lines.append(f"{p['name']}: {p.get('detailedForecast') or p.get('shortForecast')}")
    return "\n".join(lines)
