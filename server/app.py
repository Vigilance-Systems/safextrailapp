"""
Garmin MapShare -> Leaflet dashboard (Flask prototype)

Polls the MapShare raw KML feed, extracts the latest GPS point,
and serves it as JSON to a one-page Leaflet map.

SafeXTrail changes: /api/location now also returns timestamped "points"; debug/host come from env vars.

Config (environment variables):
  MAPSHARE_ID        Your MapShare name, e.g. the "MYLETTERCODE" in share.garmin.com/MYLETTERCODE
  MAPSHARE_PASSWORD  Optional; only if your MapShare page is password protected
  DEMO_MODE          Set to 1 to use sample_feed.kml instead of the live feed (handy before
                     your inReach has sent its first point)
"""
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, render_template

try:
    from dotenv import load_dotenv
    load_dotenv()   # reads server/.env (WXO_API_KEY, WXO_INSTANCE_URL, WXO_RISK_AGENT_ID, MAPSHARE_ID...)
except ImportError:
    pass            # pip install python-dotenv to use a .env file; real env vars still work

app = Flask(__name__)

MAPSHARE_ID = os.environ.get("MAPSHARE_ID", "MYLETTERCODE")
MAPSHARE_PASSWORD = os.environ.get("MAPSHARE_PASSWORD", "")
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"
FEED_URL = f"https://share.garmin.com/Feed/Share/{MAPSHARE_ID}"

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
CACHE_SECONDS = 60  # don't hammer Garmin; the device only reports every few minutes anyway
_cache = {"time": None, "data": None}


def _num(text):
    """'42.00 m from MSL' -> 42.0 ; '4.0 km/h' -> 4.0 ; None if unparseable."""
    m = re.search(r"-?[\d,]*\.?\d+", text or "")
    return float(m.group(0).replace(",", "")) if m else None


def fetch_kml() -> str:
    if DEMO_MODE:
        with open(os.path.join(os.path.dirname(__file__), "sample_feed.kml"), encoding="utf-8") as f:
            return f.read()

    # Without a d1 param, the feed returns only the most recent point.
    # Ask for the last 7 days so we can also draw a breadcrumb trail.
    d1 = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%MZ")
    auth = ("", MAPSHARE_PASSWORD) if MAPSHARE_PASSWORD else None
    r = requests.get(FEED_URL, params={"d1": d1}, auth=auth, timeout=15)
    r.raise_for_status()
    return r.text


def parse_points(kml_text: str) -> list[dict]:
    """Return every point Placemark in the feed as a dict, oldest first."""
    root = ET.fromstring(kml_text)
    points = []
    for pm in root.iter(f"{{{KML_NS['kml']}}}Placemark"):
        coords = pm.find(".//kml:Point/kml:coordinates", KML_NS)
        if coords is None or not coords.text:
            continue  # skip the LineString "track" placemark
        lon, lat, *rest = (float(v) for v in coords.text.strip().split(","))

        extra = {}
        for d in pm.findall(".//kml:ExtendedData/kml:Data", KML_NS):
            val = d.find("kml:value", KML_NS)
            extra[d.get("name")] = val.text if val is not None else None

        when = pm.find(".//kml:TimeStamp/kml:when", KML_NS)
        points.append({
            "lat": lat,
            "lon": lon,
            "time_utc": when.text if when is not None else extra.get("Time UTC"),
            "elevation": extra.get("Elevation"),
            "velocity": extra.get("Velocity"),
            "course": extra.get("Course"),
            "event": extra.get("Event"),
            "message": extra.get("Text"),
            "name": extra.get("Name"),
            "device": extra.get("Device Type"),
        })
    points.sort(key=lambda p: p["time_utc"] or "")
    return points


@app.route("/")
def index():
    return render_template("index.html", mapshare_id=MAPSHARE_ID, demo=DEMO_MODE)


@app.route("/api/location")
def api_location():
    now = datetime.now(timezone.utc)
    if _cache["time"] and (now - _cache["time"]).total_seconds() < CACHE_SECONDS:
        return jsonify(_cache["data"])

    try:
        points = parse_points(fetch_kml())
    except requests.HTTPError as e:
        return jsonify({"ok": False, "error": f"Garmin feed returned {e.response.status_code}"}), 502
    except Exception as e:  # network error, bad XML, etc.
        return jsonify({"ok": False, "error": str(e)}), 502

    data = {
        "ok": True,
        "latest": points[-1] if points else None,
        "track": [[p["lat"], p["lon"]] for p in points],
        # SafeXTrail addition: timestamped points so agents can compute "minutes stationary".
        "points": [{"time_utc": p["time_utc"], "lat": p["lat"], "lon": p["lon"],
                    "elevation_m": _num(p["elevation"]), "velocity_kmh": _num(p["velocity"]),
                    "event": p["event"], "course": p["course"], "message": p["message"],
                    "name": p["name"]} for p in points[-200:]],
        "fetched_at": now.isoformat(),
        "demo": DEMO_MODE,
    }
    _cache.update(time=now, data=data)
    return jsonify(data)


# ---- SafeXTrail REST API (/api/v1/*) ------------------------------------------------------
_pts = {"time": None, "data": []}


def get_points():
    """Parsed MapShare points, cached like /api/location so the console never hammers Garmin."""
    now = datetime.now(timezone.utc)
    if _pts["time"] and (now - _pts["time"]).total_seconds() < CACHE_SECONDS:
        return _pts["data"]
    try:
        _pts.update(time=now, data=parse_points(fetch_kml()))
    except Exception:
        _pts.update(time=now, data=_pts["data"])   # keep the last good points on a feed error
    return _pts["data"]


from api import register_api  # noqa: E402
register_api(app, points_provider=get_points, demo=DEMO_MODE)


if __name__ == "__main__":
    # HOST=0.0.0.0 only if a container/tunnel must reach it (e.g. Orchestrate Developer Edition).
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1",
            host=os.environ.get("HOST", "127.0.0.1"), port=5000)
