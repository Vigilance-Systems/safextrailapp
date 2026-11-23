"""Offline tests (no network). Run:  python -m tests.test_pipeline   (or pytest)"""
import importlib.util
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scrapers.morning_report import parse_entries          # noqa: E402
from scrapers.incident_structuring import structure        # noqa: E402
from risk_engine import engine                              # noqa: E402


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "orchestrate" / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parser_and_structuring():
    entries = parse_entries((ROOT / "tests/fixtures/deva_sample.txt").read_text())
    assert len(entries) == 12
    res = structure(entries)
    st = res["stats"]
    assert (st["kept"], st["sensitive_excluded"], st["non_hiker"], st["outside_area"]) == (5, 2, 2, 1), st
    by = {i["case_id"]: i for i in res["incidents"]}
    assert by["91-323"]["cause"] == "heat" and by["91-323"]["outcome"] == "fatal" and by["91-323"]["temp_f"] == 120
    assert by["91-323"]["incident_date"] == "1991-07-21"
    assert by["97-776"]["outcome"] == "rescued" and by["97-776"]["incident_date"] == "1998-01-04"
    assert by["98-682"]["zone"] == "dantes_view_route"
    assert "00-528" not in by and "98-778" not in by          # sensitive cases never kept
    assert all("body" not in i for i in res["incidents"])      # no narratives in output


def test_engine_scenarios():
    heat = dict(valley_feels_max_c=49, summit_feels_min_c=12, summit_gust_max_kph=25, summit_snow_cm=0, precip_mm=0)
    storm = dict(valley_feels_max_c=12, summit_feels_min_c=-18, summit_gust_max_kph=90, summit_snow_cm=15, precip_mm=8)
    calm = dict(valley_feels_max_c=28, summit_feels_min_c=4, summit_gust_max_kph=30, summit_snow_cm=0, precip_mm=0)
    low = dict(hiker_elevation_m=0, minutes_since_fix=5)
    high = dict(hiker_elevation_m=3300, minutes_since_fix=5)
    r_heat_low, r_heat_high = engine.score(heat, low), engine.score(heat, high)
    assert r_heat_low["score"] > r_heat_high["score"], "heat should matter more at low elevation"
    assert engine.score(storm, high)["score"] > engine.score(storm, low)["score"], "storm matters more at altitude"
    assert engine.score(calm, high)["score"] < 30
    assert engine.score(None, dict(hiker_elevation_m=1000, sos_active=True))["score"] == 100
    worse = engine.score(heat, dict(low, minutes_overdue=90, minutes_stationary=60))
    assert worse["score"] > r_heat_low["score"] and worse["factors"][0]["points"] > 0
    assert 0.1 <= worse["confidence"] <= 0.95


def test_tool_matches_engine():
    tool = _load_tool("score_risk")
    rnd = random.Random(1)
    for _ in range(50):
        f = dict(valley_feels_max_c=rnd.uniform(5, 52), summit_feels_min_c=rnd.uniform(-25, 15),
                 summit_gust_max_kph=rnd.uniform(0, 110), summit_snow_cm=rnd.uniform(0, 20), precip_mm=rnd.uniform(0, 15))
        elev, od, st = rnd.uniform(-80, 3400), rnd.uniform(0, 200), rnd.uniform(0, 150)
        a = tool.score_risk(hiker_elevation_m=elev, minutes_overdue=od, minutes_stationary=st, **f)
        b = engine.score(f, dict(hiker_elevation_m=elev, minutes_overdue=od, minutes_stationary=st,
                                 off_route_m=None, daylight_left_min=None))
        assert a == b


def test_position_tool_with_mock():
    tool = _load_tool("get_latest_position")
    now = datetime.now(timezone.utc)
    iso = lambda m: (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pts = [dict(time_utc=iso(m), lat=36.17 + (0.01 if m > 50 else 0.0), lon=-117.089, elevation_m=3300,
                velocity_kmh=0, event="Tracking interval received.") for m in (60, 50, 40, 30, 20, 10)]
    resp = mock.Mock(); resp.raise_for_status = lambda: None; resp.json = lambda: {"ok": True, "points": pts}
    with mock.patch.object(tool.requests, "get", return_value=resp):
        r = tool.get_latest_position(expected_return_utc=(now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert r["ok"] and 8 <= r["minutes_since_fix"] <= 12
    assert 38 <= r["minutes_stationary"] <= 42, r      # stationary since the 50-min-ago fix
    assert 44 <= r["minutes_overdue"] <= 46 and r["sos_active"] is False


def test_message_tool_and_similar():
    msg = _load_tool("finalize_hiker_message").finalize_hiker_message(
        "Storm expected at 7 PM \u2013 descend now. " + "Temperatures at the summit will fall to -18\u00b0C. " * 5)
    assert msg["length"] <= 160 and msg["requires_operator_approval"] and msg["truncated"]
    sim = _load_tool("find_similar_incidents").find_similar_incidents(hazard="heat", month=7)
    assert sim["count"] >= 1 and "heat" in sim["incidents"][0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok  ", name)
