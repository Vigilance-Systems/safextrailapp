"""
Tool: get_alerts   (diagram box 2: weather agent)

Active National Weather Service alerts at Telescope Peak (heat warnings, winter storm warnings,
flash flood, high wind...). NWS requires a User-Agent that identifies you - edit USER_AGENT.
"""
import requests

try:
    from ibm_watsonx_orchestrate.agent_builder.tools import tool
except ImportError:                      # lets you run/test the file locally without the ADK
    def tool(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


USER_AGENT = "SafeXTrail-hackathon (you@example.com)"


@tool()
def get_alerts() -> dict:
    """Get active National Weather Service alerts for the Telescope Peak area.

    Returns:
        A dict with ok, count and alerts (event, severity, headline, ends). On failure: ok=false and error.
    """
    try:
        r = requests.get("https://api.weather.gov/alerts/active", params={"point": "36.170,-117.089"},
                         headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"}, timeout=30)
        r.raise_for_status()
        feats = r.json().get("features", [])
    except Exception as exc:
        return {"ok": False, "error": "NWS alerts unavailable: %s" % exc}
    alerts = [{"event": f["properties"].get("event"), "severity": f["properties"].get("severity"),
               "headline": (f["properties"].get("headline") or "")[:200],
               "ends": f["properties"].get("ends")} for f in feats]
    return {"ok": True, "count": len(alerts), "alerts": alerts}
