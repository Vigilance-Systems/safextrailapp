"""
Tool: get_forecast   (diagram box 2: "Real-Time Weather Agent")

Pulls the next ~36 h of daily forecast from Open-Meteo for two Telescope-area sites and reduces it
to the five model features, taking the WORST single day for each (same definitions as training).
Also returns daylight_left_min (minutes until sunset at the summit). No API key required.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

try:
    from ibm_watsonx_orchestrate.agent_builder.tools import tool
except ImportError:                      # lets you run/test the file locally without the ADK
    def tool(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


URL = "https://api.open-meteo.com/v1/forecast"
TZ = "America/Los_Angeles"
SITES = {"summit": (36.170, -117.089, 3368), "valley": (36.230, -116.767, -86)}
DAILY = "apparent_temperature_max,apparent_temperature_min,wind_gusts_10m_max,snowfall_sum,precipitation_sum,sunset"


def _get(site):
    lat, lon, elev = SITES[site]
    r = requests.get(URL, params={"latitude": lat, "longitude": lon, "elevation": elev,
                                  "daily": DAILY, "forecast_days": 2, "timezone": TZ}, timeout=30)
    r.raise_for_status()
    return r.json()["daily"]


def _clean(xs):
    return [x for x in xs if x is not None]


@tool()
def get_forecast() -> dict:
    """Get the Telescope Peak area weather forecast as risk-model features.

    Returns:
        A dict with ok, features (valley_feels_max_c, summit_feels_min_c, summit_gust_max_kph,
        summit_snow_cm, precip_mm), daylight_left_min, valid_for and source. On failure: ok=false and error.
        Copy the features values exactly when calling score_risk.
    """
    try:
        v, s = _get("valley"), _get("summit")
        features = {
            "valley_feels_max_c": max(_clean(v["apparent_temperature_max"])),
            "summit_feels_min_c": min(_clean(s["apparent_temperature_min"])),
            "summit_gust_max_kph": max(_clean(s["wind_gusts_10m_max"])),
            "summit_snow_cm": max(_clean(s["snowfall_sum"]) or [0]),
            "precip_mm": max(_clean(s["precipitation_sum"]) or [0]),
        }
        now = datetime.now(ZoneInfo(TZ))
        sunset = datetime.fromisoformat(s["sunset"][0]).replace(tzinfo=ZoneInfo(TZ))
        daylight = max(0, round((sunset - now).total_seconds() / 60))
    except Exception as exc:
        return {"ok": False, "error": "forecast unavailable: %s" % exc}
    return {"ok": True, "features": {k: round(x, 1) for k, x in features.items()},
            "daylight_left_min": daylight, "valid_for": "today and tomorrow (worst single day)",
            "source": "Open-Meteo"}
