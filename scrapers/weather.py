"""
Step 3 - Weather data for the Telescope Peak area (Open-Meteo + NWS alerts).

Two sites are sampled because the hazards differ enormously with elevation:
  * VALLEY  - Badwater Basin, -86 m: extreme heat
  * SUMMIT  - Telescope Peak, 3,368 m (11,049 ft): cold, wind, snow
Open-Meteo is a gridded model, so we pass `elevation` to get values downscaled to the real height.

The SAME five daily variables are used for history (archive API, for training) and for the forecast
(for live scoring). That symmetry matters: the model must see the same feature definitions in both.

Features produced (see risk_engine.engine.FEATURES):
  valley_feels_max_c   daily max apparent temperature at the valley floor
  summit_feels_min_c   daily min apparent temperature at the summit
  summit_gust_max_kph  daily max wind gust at the summit
  summit_snow_cm       daily snowfall at the summit
  precip_mm            daily precipitation at the summit

No API key needed. Open-Meteo's free tier is for non-commercial use; NWS requires a User-Agent.
"""
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
NWS_USER_AGENT = "SafeXTrail-hackathon (you@example.com)"   # edit me

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARS = "apparent_temperature_max,apparent_temperature_min,wind_gusts_10m_max,snowfall_sum,precipitation_sum"
TZ = "America/Los_Angeles"


@dataclass(frozen=True)
class Site:
    name: str
    lat: float
    lon: float
    elevation_m: float


SUMMIT = Site("summit", 36.170, -117.089, 3368)
VALLEY = Site("valley", 36.230, -116.767, -86)


def _daily_frame(payload: dict) -> pd.DataFrame:
    df = pd.DataFrame(payload["daily"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def fetch_archive(site: Site, start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Daily history for one site. start/end are 'YYYY-MM-DD'. Cached to data/raw/."""
    cache = RAW / f"weather_{site.name}_{start}_{end}.csv"
    if use_cache and cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    r = requests.get(ARCHIVE_URL, params={
        "latitude": site.lat, "longitude": site.lon, "elevation": site.elevation_m,
        "start_date": start, "end_date": end, "daily": DAILY_VARS, "timezone": TZ}, timeout=120)
    r.raise_for_status()
    df = _daily_frame(r.json())
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def build_feature_frame(start: str, end: str) -> pd.DataFrame:
    """One row per day with the five model features. Rows with missing values are dropped."""
    v, s = fetch_archive(VALLEY, start, end), fetch_archive(SUMMIT, start, end)
    out = pd.DataFrame({
        "valley_feels_max_c": v["apparent_temperature_max"],
        "summit_feels_min_c": s["apparent_temperature_min"],
        "summit_gust_max_kph": s["wind_gusts_10m_max"],
        "summit_snow_cm": s["snowfall_sum"],
        "precip_mm": s["precipitation_sum"],
    })
    return out.dropna()


def fetch_forecast(site: Site, days: int = 2) -> dict:
    r = requests.get(FORECAST_URL, params={
        "latitude": site.lat, "longitude": site.lon, "elevation": site.elevation_m,
        "daily": DAILY_VARS + ",sunset", "forecast_days": days, "timezone": TZ}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_nws_alerts(lat: float = SUMMIT.lat, lon: float = SUMMIT.lon) -> list:
    """Active NWS alerts (heat warnings, winter storm warnings, ...) at a point."""
    r = requests.get("https://api.weather.gov/alerts/active", params={"point": f"{lat},{lon}"},
                     headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"}, timeout=30)
    r.raise_for_status()
    return [{"event": f["properties"].get("event"), "severity": f["properties"].get("severity"),
             "headline": f["properties"].get("headline"), "ends": f["properties"].get("ends")}
            for f in r.json().get("features", [])]
