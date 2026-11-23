"""
Adventurer roster + helpers.

* ADV-1 is the REAL inReach (from the MapShare feed). Its state is derived from the live track.
* The others are SEEDED DEMO records so the console looks like the design mock-up.
  Their positions/states are invented: replace with real devices or a database later.
"""
import math
import re
from datetime import datetime, timezone

LIVE_ID = "adv-1"
SUMMIT = (36.170, -117.089)   # Telescope Peak


def num(text):
    """'42.00 m from MSL' -> 42.0, '4.0 km/h' -> 4.0, None if unparseable."""
    m = re.search(r"-?[\d,]*\.?\d+", text or "")
    return float(m.group(0).replace(",", "")) if m else None


def parse_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(a))


def derive_live_state(points, expected_return_utc="", route=None, still_radius_m=75):
    """Turn the raw MapShare points into position / telemetry / hiker-state dicts."""
    last = points[-1]
    now = datetime.now(timezone.utc)
    last_t = parse_utc(last["time_utc"])
    still_since = last_t
    for p in reversed(points):
        if haversine_m(p["lat"], p["lon"], last["lat"], last["lon"]) <= still_radius_m:
            still_since = parse_utc(p["time_utc"])
        else:
            break
    off_route = None
    if route:
        off_route = round(min(haversine_m(last["lat"], last["lon"], a, b) for a, b in route))
    overdue = 0
    if expected_return_utc:
        try:
            overdue = max(0, round((now - parse_utc(expected_return_utc)).total_seconds() / 60))
        except ValueError:
            pass
    ev = (last.get("event") or "")
    elev = num(last.get("elevation"))
    kmh = num(last.get("velocity"))
    return {
        "position": {"lat": last["lat"], "lon": last["lon"], "elevation_m": elev,
                     "elevation_ft": None if elev is None else round(elev * 3.28084),
                     "fix_time_utc": last["time_utc"]},
        "telemetry": {"speed_mph": None if kmh is None else round(kmh * 0.621371, 1),
                      "course_deg": num(last.get("course")), "message": last.get("message"),
                      "event": ev, "sos_active": ("sos" in ev.lower() or "emergency" in ev.lower())},
        "state": {"minutes_since_fix": round((now - last_t).total_seconds() / 60),
                  "minutes_stationary": round((last_t - still_since).total_seconds() / 60),
                  "minutes_overdue": overdue, "off_route_m": off_route},
    }


# id, name, party, route, (dlat, dlon) offset from summit, elevation_m, overdue, stationary, off_route_m, since_fix_min
DEMO_ROSTER = [
    ("adv-7",  "ADVENTURER 7",  "solo, day hike",  "Telescope Peak trail",  (-0.010,  0.004), 2900,  0, 38,   0,  2),
    ("adv-4",  "ADVENTURER 4",  "2 ppl",           "Badwater route",        ( 0.060,  0.320),  -50, 42,  5, 180, 42),
    ("adv-12", "ADVENTURER 12", "solo",            "Mahogany Flat",         ( 0.040, -0.020), 2400,  0,  0,   0,  1),
    ("adv-15", "ADVENTURER 15", "solo",            "Wildrose Peak",         ( 0.080, -0.060), 2300,  0,  0,   0,  4),
    ("adv-2",  "ADVENTURER 2",  "2 ppl",           "Telescope Peak trail",  (-0.004, -0.002), 3100,  0,  0,   0,  0),
    ("adv-3",  "ADVENTURER 3",  "group of 3",      "Telescope Peak trail",  (-0.015,  0.010), 2700,  0,  0,   0,  1),
    ("adv-21", "ADVENTURER 21", "camped",          "Mahogany Flat camp",    ( 0.052, -0.016), 2450,  0,  0,   0,  2),
    ("adv-9",  "ADVENTURER 9",  "day hike",        "Dante's View",          ( 0.020,  0.240), 1650,  0,  0,   0,  0),
]
