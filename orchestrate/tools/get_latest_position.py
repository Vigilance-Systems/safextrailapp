"""
Tool: get_latest_position   (diagram box 3 -> 5: "Live positions + trip plans")

Reads the SafeXTrail Flask server (server/app.py, which polls the Garmin MapShare KML feed) and
turns the raw track into the hiker-state numbers the risk model needs:
  minutes_since_fix, minutes_stationary, minutes_overdue, off_route_m, sos_active, elevation.

Config (environment variable, or an Orchestrate connection in production):
  SAFEXTRAIL_API_URL   base URL of the Flask server. NOTE: Orchestrate (Developer Edition runs in
                       Docker; the cloud runs remotely) cannot reach "localhost" on your laptop.
                       Use http://host.docker.internal:5000 for Developer Edition, or a tunnel
                       (ngrok / cloudflared) or a deployed server for anything else.
"""
import math
import os
from datetime import datetime, timezone

import requests

try:
    from ibm_watsonx_orchestrate.agent_builder.tools import tool
except ImportError:                      # lets you run/test the file locally without the ADK
    def tool(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


API_URL = os.environ.get("SAFEXTRAIL_API_URL", "https://safextrail.onrender.com")
STATIONARY_RADIUS_M = 75          # movement smaller than this between fixes counts as "not moving"
# Planned route as (lat, lon) pairs - export from a GPX file. Empty = off-route check is skipped.
ROUTE_POINTS = []


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@tool()
def get_latest_position(expected_return_utc: str = "") -> dict:
    """Get the hiker's latest GPS fix and derived safety signals from the SafeXTrail server.

    Args:
        expected_return_utc: Planned return time as an ISO-8601 UTC string, e.g. 2026-11-19T23:30:00Z.
            Leave empty if unknown; minutes_overdue is then 0.

    Returns:
        A dict with ok, lat, lon, elevation_m, fix_time_utc, minutes_since_fix, minutes_stationary,
        minutes_overdue, off_route_m (-1 if unknown), sos_active and event. On failure: ok=false and error.
    """
    try:
        r = requests.get(API_URL.rstrip("/") + "/api/location", timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return {"ok": False, "error": "position service unreachable: %s" % exc}
    pts = data.get("points") or []
    if not data.get("ok") or not pts:
        return {"ok": False, "error": data.get("error", "no GPS points received yet")}

    now = datetime.now(timezone.utc)
    last = pts[-1]
    last_t = _parse_utc(last["time_utc"])

    # How long has the hiker stayed within STATIONARY_RADIUS_M of the latest fix?
    still_since = last_t
    for p in reversed(pts):
        if _haversine_m(p["lat"], p["lon"], last["lat"], last["lon"]) <= STATIONARY_RADIUS_M:
            still_since = _parse_utc(p["time_utc"])
        else:
            break

    off_route = -1
    if ROUTE_POINTS:   # vertex distance is fine for dense GPX tracks
        off_route = round(min(_haversine_m(last["lat"], last["lon"], la, lo) for la, lo in ROUTE_POINTS))

    overdue = 0
    if expected_return_utc:
        try:
            overdue = max(0, round((now - _parse_utc(expected_return_utc)).total_seconds() / 60))
        except ValueError:
            pass

    event = (last.get("event") or "")
    return {
        "ok": True, "lat": last["lat"], "lon": last["lon"],
        "elevation_m": last.get("elevation_m"),
        "fix_time_utc": last["time_utc"],
        "minutes_since_fix": round((now - last_t).total_seconds() / 60),
        "minutes_stationary": round((last_t - still_since).total_seconds() / 60),
        "minutes_overdue": overdue,
        "off_route_m": off_route,
        # Verify the real event wording on your device's feed; this is a loose match.
        "sos_active": ("sos" in event.lower() or "emergency" in event.lower()),
        "event": event,
    }
