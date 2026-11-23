"""
REST API behind the SAR console.   Base path: /api/v1

  GET  /health                               config check (is Orchestrate set up? demo mode?)
  GET  /adventurers                          roster sorted by risk, highest first
  GET  /adventurers/<id>                     full detail: position, telemetry, risk + breakdown, track
  POST /adventurers/<id>/assess              ask the watsonx Orchestrate risk_agent (falls back to the local engine)
  POST /agent/chat                           free-form chat with risk_agent (keeps the thread)
  GET  /events                               live event feed (risk changes, overdue, stationary)
  POST /adventurers/<id>/actions/<action>    dispatch | message | alert-sar  -> queued for HUMAN approval only

Two speeds, on purpose:
  * FAST path  - roster scores come from the local risk engine (same code the Orchestrate score_risk
                 tool uses). Cheap and instant, so it can refresh every 30 s for every adventurer.
  * SLOW path  - /assess calls the Orchestrate agent (LLM + tools, 10-60 s) only when an operator asks.
"""
import re
import sys
import time
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # finds ../risk_engine
from risk_engine import engine                                         # noqa: E402

from orchestrate_client import OrchestrateClient, OrchestrateError     # noqa: E402
from roster import DEMO_ROSTER, LIVE_ID, SUMMIT, derive_live_state     # noqa: E402

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

_client = OrchestrateClient()
_points_provider = lambda: []        # set by register_api()
_demo = False
_events = deque(maxlen=60)
_last = {}                           # id -> last level, for change detection
_flags = set()                       # (id, flag) already announced
_audit = []                          # approved operator actions (in memory; use a DB for real)
_lock = threading.Lock()
_weather = {"t": 0.0, "ok_until": 0.0, "data": None}


def register_api(app, points_provider, demo=False):
    global _points_provider, _demo
    _points_provider, _demo = points_provider, demo
    app.register_blueprint(bp)


# --------------------------------------------------------------------------- weather
def current_weather():
    """Same Open-Meteo logic as orchestrate/tools/get_forecast.py, cached 10 min (all hikers share it)."""
    now = time.time()
    if now < _weather["ok_until"]:
        return _weather["data"]
    sites = {"summit": (36.170, -117.089, 3368), "valley": (36.230, -116.767, -86)}
    daily = "apparent_temperature_max,apparent_temperature_min,wind_gusts_10m_max,snowfall_sum,precipitation_sum,sunset"
    try:
        out = {}
        for k, (lat, lon, el) in sites.items():
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
                "latitude": lat, "longitude": lon, "elevation": el, "daily": daily,
                "forecast_days": 2, "timezone": "America/Los_Angeles"})
            r.raise_for_status()
            out[k] = r.json()["daily"]
        clean = lambda xs: [x for x in xs if x is not None]
        v, s = out["valley"], out["summit"]
        data = {"features": {
            "valley_feels_max_c": max(clean(v["apparent_temperature_max"])),
            "summit_feels_min_c": min(clean(s["apparent_temperature_min"])),
            "summit_gust_max_kph": max(clean(s["wind_gusts_10m_max"])),
            "summit_snow_cm": max(clean(s["snowfall_sum"]) or [0]),
            "precip_mm": max(clean(s["precipitation_sum"]) or [0])}}
        ttl = 600
    except Exception:
        data, ttl = None, 60                       # remember the failure briefly, don't hammer the API
    _weather.update(t=now, ok_until=now + ttl, data=data)
    return data


# --------------------------------------------------------------------------- scoring
BUCKET = {"overdue": "schedule", "low_daylight": "schedule", "stationary": "movement", "off_route": "movement"}


def score_hiker(elev_m, state):
    w = current_weather()
    res = engine.score(w["features"] if w else None, dict(state, hiker_elevation_m=elev_m or 1500.0))
    breakdown = {"weather": 0.0, "movement": 0.0, "schedule": 0.0}
    for f in res["factors"]:
        breakdown[BUCKET.get(f["name"], "weather")] += f["points"]
    res["breakdown"] = {k: round(v, 1) for k, v in breakdown.items()}
    return res


def _emit(hid, tag, text):
    _events.appendleft({"time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"), "tag": tag,
                        "adventurer": hid, "text": text})


def _announce_changes(h):
    with _lock:
        lvl, prev = h["risk"]["level"], _last.get(h["id"])
        if prev is not None and prev != lvl:
            _emit(h["id"], "RISK", "%s %s -> %s (score %d): %s" % (h["name"], prev, lvl, h["risk"]["score"], h["risk"]["summary"]))
        _last[h["id"]] = lvl
        st = h["state"]
        for flag, cond, tag, txt in (
                ("overdue", st["minutes_overdue"] > 0, "OVERDUE", "%s is overdue by %d min" % (h["name"], st["minutes_overdue"])),
                ("stationary", st["minutes_stationary"] >= 30, "STATIONARY", "%s has not moved for %d min" % (h["name"], st["minutes_stationary"]))):
            if cond and (h["id"], flag) not in _flags:
                _flags.add((h["id"], flag)); _emit(h["id"], tag, txt)
            if not cond:
                _flags.discard((h["id"], flag))


def build_adventurer(hid, expected_return_utc=""):
    """Full record for one adventurer, or None if unknown / no data yet."""
    if hid == LIVE_ID:
        pts = _points_provider()
        if not pts:
            return {"id": hid, "name": "ADVENTURER 1", "live": True, "position": None, "risk": None,
                    "status_text": "Awaiting first GPS fix"}
        d = derive_live_state(pts, expected_return_utc)
        h = {"id": hid, "name": "ADVENTURER 1", "live": True, "party": "solo", "route": "inReach live track",
             "track": [[p["lat"], p["lon"]] for p in pts], **d}
    else:
        row = next((r for r in DEMO_ROSTER if r[0] == hid), None)
        if not row:
            return None
        _id, name, party, route, (dlat, dlon), elev, od, st, off, since = row
        h = {"id": _id, "name": name, "live": False, "party": party, "route": route,
             "position": {"lat": round(SUMMIT[0] + dlat, 5), "lon": round(SUMMIT[1] + dlon, 5), "elevation_m": elev,
                          "elevation_ft": round(elev * 3.28084), "fix_time_utc": None},
             "telemetry": {"speed_mph": None, "course_deg": None, "message": None, "event": "", "sos_active": False},
             "state": {"minutes_since_fix": since, "minutes_stationary": st, "minutes_overdue": od,
                       "off_route_m": off or None}}
    state = dict(h["state"], sos_active=h["telemetry"]["sos_active"], daylight_left_min=None)
    h["risk"] = score_hiker(h["position"]["elevation_m"], state)
    top = h["risk"]["factors"][0]["text"] if h["risk"]["factors"] else "On schedule"
    h["status_text"] = "%s · %s · %s" % (h["route"], h["party"], top)
    _announce_changes(h)
    return h


def summary(h):
    r = h.get("risk") or {}
    return {"id": h["id"], "name": h["name"], "live": h.get("live", False), "status_text": h["status_text"],
            "score": r.get("score"), "level": r.get("level"),
            "fix_age_min": (h.get("state") or {}).get("minutes_since_fix"),
            "position": h.get("position")}


def ok(payload, code=200):
    return jsonify(dict(payload, ok=True)), code


def err(msg, code):
    return jsonify({"ok": False, "error": msg}), code


# --------------------------------------------------------------------------- agent reply parsing
HEADER_RE = re.compile(r"^\s*[*#]*\s*(SCORE|WHY|CONTEXT|ACTION|DRAFT MESSAGE \(NOT SENT\))\s*[*]*\s*:\s*[*]*\s*", re.I | re.M)


def parse_agent_reply(text):
    """risk_agent answers 'SCORE / WHY / CONTEXT / ACTION / DRAFT MESSAGE (NOT SENT)'. Split it for the UI.
    If the model drifts from that format, the raw text is still returned, so nothing is lost."""
    out = {"sos": text.strip().upper().startswith("SOS ACTIVE"), "score": None, "level": None, "confidence": None,
           "why": [], "context": [], "action": "", "draft_message": None}
    ms = list(HEADER_RE.finditer(text))
    sections = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        sections[m.group(1).upper()] = text[m.end():end].strip()
    s = sections.get("SCORE", "")
    m = re.search(r"(\d{1,3})\s*/\s*100\s*([A-Za-z]+)?", s)
    if m:
        out["score"], out["level"] = int(m.group(1)), (m.group(2) or "").upper() or None
    m = re.search(r"confidence\s*[:=]?\s*([0-9.]+)", s, re.I)
    if m:
        out["confidence"] = float(m.group(1))
    lines = lambda t: [re.sub(r"^[\s\-*\d.)]+", "", ln).strip() for ln in t.splitlines() if ln.strip()]
    out["why"], out["context"] = lines(sections.get("WHY", "")), lines(sections.get("CONTEXT", ""))
    out["action"] = sections.get("ACTION", "")
    d = sections.get("DRAFT MESSAGE (NOT SENT)", "").strip().strip('"')
    out["draft_message"] = None if (not d or d.lower().startswith("none")) else d
    return out


# --------------------------------------------------------------------------- routes
@bp.get("/health")
def health():
    return ok({"orchestrate_configured": _client.configured, "demo_mode": _demo,
               "weather_ok": current_weather() is not None})


@bp.get("/adventurers")
def adventurers():
    ids = [LIVE_ID] + [r[0] for r in DEMO_ROSTER]
    rows = [summary(h) for h in (build_adventurer(i) for i in ids) if h]
    rows.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1))
    return ok({"count": len(rows), "adventurers": rows})


@bp.get("/adventurers/<hid>")
def adventurer(hid):
    h = build_adventurer(hid, request.args.get("expected_return_utc", ""))
    return ok({"adventurer": h}) if h else err("unknown adventurer", 404)


@bp.post("/adventurers/<hid>/assess")
def assess(hid):
    body = request.get_json(silent=True) or {}
    ret = (body.get("expected_return_utc") or "").strip()
    h = build_adventurer(hid, ret)
    if not h or not h.get("risk"):
        return err("no data for this adventurer yet", 404)
    if hid == LIVE_ID and _client.configured:         # only ADV-1 has real tools behind the agent
        prompt = "Assess the hiker" + (", expected back at %s." % ret if ret else ".")
        try:
            r = _client.chat(prompt)
        except OrchestrateError as exc:
            return err(str(exc), 502)
        return ok({"source": "orchestrate", "thread_id": r["thread_id"], "text": r["text"],
                   "assessment": parse_agent_reply(r["text"])})
    note = ("Orchestrate not configured (set WXO_* in .env); showing the local risk engine instead."
            if hid == LIVE_ID else "Demo adventurer: scored by the local risk engine, not the agent.")
    r = h["risk"]
    return ok({"source": "local", "note": note, "text": r["summary"], "assessment": {
        "sos": h["telemetry"]["sos_active"], "score": r["score"], "level": r["level"], "confidence": r["confidence"],
        "why": [f["text"] for f in r["factors"][:3]], "context": [r["model_note"]],
        "action": r["recommended_action"], "draft_message": None}})


@bp.post("/agent/chat")
def agent_chat():
    body = request.get_json(silent=True) or {}
    msg = (body.get("message") or "").strip()
    if not msg or len(msg) > 2000:
        return err("message required (max 2000 characters)", 400)
    try:
        r = _client.chat(msg, thread_id=body.get("thread_id") or None)
    except OrchestrateError as exc:
        return err(str(exc), 502 if _client.configured else 503)
    return ok({"text": r["text"], "thread_id": r["thread_id"], "assessment": parse_agent_reply(r["text"])})


@bp.get("/events")
def events():
    build_adventurer(LIVE_ID)                          # refresh so new changes show up
    limit = min(int(request.args.get("limit", 30)), 60)
    return ok({"events": list(_events)[:limit]})


@bp.post("/adventurers/<hid>/actions/<action>")
def action(hid, action):
    """Nothing is sent from here. A named human must approve; the request is logged and queued.
    TODO: wire 'dispatch' / 'alert-sar' to your paging system and 'message' to Garmin messaging."""
    if action not in ("dispatch", "message", "alert-sar"):
        return err("unknown action", 404)
    body = request.get_json(silent=True) or {}
    who = (body.get("approved_by") or "").strip()
    if not who:
        return err("approved_by is required: a human operator must approve every action", 400)
    if action == "message":
        text = " ".join((body.get("text") or "").split())
        if not text or len(text) > 160 or not text.isascii():
            return err("message text required: max 160 plain ASCII characters (satellite limit)", 400)
    entry = {"time_utc": datetime.now(timezone.utc).isoformat(), "adventurer": hid, "action": action,
             "approved_by": who, "text": body.get("text"), "note": body.get("note")}
    _audit.append(entry)
    _emit(hid, "ACTION", "%s queued by %s" % (action, who))
    return ok({"status": "queued_for_dispatch", "entry": entry}, 202)
