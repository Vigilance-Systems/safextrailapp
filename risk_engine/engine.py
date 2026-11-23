"""
SafeXTrail risk engine - pure Python (standard library only) so it can be pasted into an
Orchestrate tool unchanged. `scripts/sync_tools.py` copies THIS file into orchestrate/tools/score_risk.py,
so there is exactly one implementation to test.

How the 0-100 score is built
----------------------------
score = weather_points (0-55) + hiker_points (0-45)      [SOS event overrides to 100]

1. WEATHER (max 55 points) - "how unusual and hazardous is today's weather, historically?"
   * Five features (see FEATURES) are standardized: z = (x - mean) / std.
   * A logistic-regression model trained in the notebook gives each feature a coefficient.
     contribution_i = coef_i * z_i * exposure_i
   * exposure_i is an expert adjustment for WHERE the hiker is: valley-floor heat matters at low
     elevation, summit cold/wind/snow matter high up (1.0 vs 0.2 at the extremes).
   * logit = intercept + sum(contributions). We convert the logit to a PERCENTILE against all
     historical days used in training ("this day is worse than 93% of days since 1989").
     weather_points = 55 * percentile.
   * Points are split across factors in proportion to their positive contributions - that split IS
     the explanation, and it is exact (no SHAP needed).

2. HIKER STATE (max 45 points) - transparent rules, not trained (we have no labelled trip data):
   overdue (<=20), stationary (<=15), off planned route (<=8), low daylight (<=7).

It is decision support: the weather part is a RELATIVE hazard (we only know incident days, not how
many hikers were out), so never present the score as an absolute probability.
"""
import json
import math
import os
from bisect import bisect_left
from typing import Dict, List, Optional

FEATURES = ("valley_feels_max_c", "summit_feels_min_c", "summit_gust_max_kph",
            "summit_snow_cm", "precip_mm")
HEAT_FEATURES = ("valley_feels_max_c",)
COLD_FEATURES = ("summit_feels_min_c", "summit_gust_max_kph", "summit_snow_cm")
# Direction in which each feature raises hazard (+1 = higher value is riskier, -1 = lower is riskier).
EXPECTED_SIGN = {"valley_feels_max_c": 1, "summit_feels_min_c": -1, "summit_gust_max_kph": 1,
                 "summit_snow_cm": 1, "precip_mm": 1}
# Expert prior coefficients (per standardized unit). The notebook shrinks learned values toward these.
PRIOR_COEFS = {"valley_feels_max_c": 0.9, "summit_feels_min_c": -0.6, "summit_gust_max_kph": 0.35,
               "summit_snow_cm": 0.5, "precip_mm": 0.15}
W_MAX = 55.0
S_MAX = 45.0
LEVELS = ((75, "CRITICAL"), (55, "HIGH"), (30, "MODERATE"), (0, "LOW"))
ACTIONS = {
    "LOW": "Routine monitoring.",
    "MODERATE": "Poll more often; consider a safety message to the hiker.",
    "HIGH": "Message the hiker and put the SAR duty officer on standby.",
    "CRITICAL": "Operator review for dispatch now.",
}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def load_coefficients(path: Optional[str] = None) -> dict:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "coefficients.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def exposure(feature: str, elevation_m: float) -> float:
    """How relevant a hazard is at the hiker's elevation (expert prior, not learned)."""
    t = _clamp((elevation_m - 1200.0) / (2500.0 - 1200.0), 0.0, 1.0)
    if feature in HEAT_FEATURES:
        return 1.0 - 0.8 * t
    if feature in COLD_FEATURES:
        return 0.2 + 0.8 * t
    return 1.0


def _f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _weather_text(feature: str, v: float) -> str:
    if feature == "valley_feels_max_c":
        return "Valley floor feels like %d C (%d F) at its peak" % (round(v), round(_f(v)))
    if feature == "summit_feels_min_c":
        return "Summit feels as cold as %d C (%d F)" % (round(v), round(_f(v)))
    if feature == "summit_gust_max_kph":
        return "Summit wind gusts up to %.0f km/h" % v
    if feature == "summit_snow_cm":
        return "%.0f cm of snow forecast at the summit" % v
    return "%.0f mm of precipitation forecast" % v


def percentile(logit: float, quantiles: List[float]) -> float:
    """Fraction of historical days with a lower hazard logit (linear interpolation, 0..1)."""
    if logit <= quantiles[0]:
        return 0.0
    if logit >= quantiles[-1]:
        return 1.0
    i = bisect_left(quantiles, logit)
    lo, hi = quantiles[i - 1], quantiles[i]
    frac = (logit - lo) / (hi - lo) if hi > lo else 0.0
    return (i - 1 + frac) / (len(quantiles) - 1)


def weather_component(features: Dict[str, float], elevation_m: float, coeffs: dict) -> dict:
    contribs, hazard_z = {}, {}
    for name in FEATURES:
        z = (features[name] - coeffs["means"][name]) / coeffs["stds"][name]
        coef = coeffs["coefs"][name]
        if coef * EXPECTED_SIGN[name] < 0:        # physically backwards coefficient: ignore it (safety net)
            coef = 0.0
        contribs[name] = coef * z * exposure(name, elevation_m)
        hazard_z[name] = z * EXPECTED_SIGN[name]  # > 0 means "worse than average" for this feature
    logit = coeffs["intercept"] + sum(contribs.values())
    pct = percentile(logit, coeffs["logit_quantiles"])
    points = W_MAX * pct
    positive = {k: v for k, v in contribs.items() if v > 0}
    total = sum(positive.values())
    factors, baseline = [], 0.0
    for name, c in positive.items():
        share = points * c / total if total > 0 else 0.0
        if hazard_z[name] >= 0.5 and share >= 0.5:    # only name a factor if it is genuinely unusual
            factors.append({"name": name, "kind": "weather", "value": round(features[name], 1),
                            "points": round(share, 1), "text": _weather_text(name, features[name])})
        else:
            baseline += share
    if total <= 0:
        baseline = points
    if baseline >= 1.0:
        factors.append({"name": "baseline", "kind": "weather", "value": 0, "points": round(baseline, 1),
                        "text": "Typical seasonal conditions (no single weather driver)"})
    return {"logit": logit, "percentile": pct, "points": points, "factors": factors}


def hiker_component(state: dict) -> List[dict]:
    out = []
    od = state.get("minutes_overdue") or 0
    if od > 0:
        out.append({"name": "overdue", "kind": "hiker", "value": round(od),
                    "points": round(min(20.0, od / 6.0), 1),
                    "text": "Check-in overdue by %d min" % od})
    st = state.get("minutes_stationary") or 0
    if st >= 20:
        out.append({"name": "stationary", "kind": "hiker", "value": round(st),
                    "points": round(min(15.0, (st - 20.0) / 4.0), 1),
                    "text": "No movement for %d min" % st})
    off = state.get("off_route_m")
    if off is not None and off > 150:
        out.append({"name": "off_route", "kind": "hiker", "value": round(off),
                    "points": round(min(8.0, (off - 150.0) / 100.0), 1),
                    "text": "%d m off the planned route" % off})
    dl = state.get("daylight_left_min")
    if dl is not None and dl < 90:
        out.append({"name": "low_daylight", "kind": "hiker", "value": round(max(dl, 0)),
                    "points": round(7.0 * (1.0 - max(dl, 0) / 90.0), 1),
                    "text": ("Sunset has passed" if dl <= 0 else "Only %d min of daylight left" % dl)})
    return [f for f in out if f["points"] > 0]


def level_for(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "LOW"


def _dominant_hazard(factors: List[dict]) -> str:
    if not factors:
        return "none"
    top = max(factors, key=lambda f: f["points"])
    if top["kind"] == "hiker":
        return "lost"
    return {"valley_feels_max_c": "heat", "summit_feels_min_c": "cold", "summit_snow_cm": "cold",
            "summit_gust_max_kph": "wind", "precip_mm": "storm"}.get(top["name"], "none")


def _model_note(coeffs: dict) -> str:
    if coeffs.get("is_prior"):
        return "PRIOR expert coefficients (not trained on data yet)."
    base = ("Weather hazard is a relative percentile vs %s historical days, learned from %s Telescope-area "
            "incidents. It is not an absolute probability." % (coeffs.get("n_days"), coeffs.get("n_incidents")))
    if coeffs.get("synthetic"):
        return "WARNING: trained on SYNTHETIC demo weather. " + base
    return base


def score(features: Optional[Dict[str, float]], state: dict, coeffs: Optional[dict] = None) -> dict:
    """features: dict with FEATURES keys, or None if the forecast is unavailable.
    state: hiker_elevation_m, minutes_overdue, minutes_stationary, off_route_m (None ok),
           daylight_left_min (None ok), minutes_since_fix, sos_active."""
    coeffs = coeffs or load_coefficients()
    elev = state.get("hiker_elevation_m")
    elev = 0.0 if elev is None else float(elev)

    factors, wpts, pct = [], 0.0, None
    if features is not None:
        w = weather_component(features, elev, coeffs)
        factors += w["factors"]
        wpts, pct = w["points"], w["percentile"]
    hf = hiker_component(state)
    factors += hf
    hpts = min(S_MAX, sum(f["points"] for f in hf))

    total = _clamp(wpts + hpts, 0.0, 100.0)
    if state.get("sos_active"):
        total = 100.0
        factors.insert(0, {"name": "sos_active", "kind": "hiker", "value": 1, "points": 100.0,
                           "text": "Device reported an SOS / emergency event"})
    factors.sort(key=lambda f: -f["points"])

    conf = 0.85 - min(0.4, (state.get("minutes_since_fix") or 0) / 300.0)
    if features is None:
        conf -= 0.25
    if coeffs.get("is_prior") or coeffs.get("synthetic"):
        conf = min(conf, 0.5)
    elif (coeffs.get("n_incidents") or 0) < 30:
        conf = min(conf, 0.7)
    conf = _clamp(conf, 0.1, 0.95)

    level = level_for(total)
    top = [f["text"] for f in factors[:3]]
    summary = "%s (%d/100)" % (level, round(total))
    if top:
        summary += ": " + "; ".join(top)
    return {
        "score": int(round(total)), "level": level, "confidence": round(conf, 2),
        "weather_percentile": None if pct is None else round(pct, 3),
        "weather_points": round(wpts, 1), "hiker_points": round(hpts, 1),
        "factors": factors[:6], "summary": summary,
        "dominant_hazard": _dominant_hazard(factors),
        "recommended_action": ACTIONS[level],
        "weather_available": features is not None,
        "model_note": _model_note(coeffs),
    }
