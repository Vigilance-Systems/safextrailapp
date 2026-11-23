"""
Tool: find_similar_incidents   (diagram box 2: "Custom Trained Model" -> history)

Looks up past Telescope-area incidents that resemble the current situation, so the explanation can
say "similar conditions preceded a heat fatality in July 1991". The INCIDENTS list is GENERATED from
data/incidents.json by scripts/sync_tools.py and holds only derived fields (no narratives or names).
"""
from datetime import datetime

try:
    from ibm_watsonx_orchestrate.agent_builder.tools import tool
except ImportError:                      # lets you run/test the file locally without the ADK
    def tool(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


# ===== BEGIN GENERATED: incidents (from data/incidents.json) =====
INCIDENTS = [{'case_id': '88-68',
  'incident_date': '1988-04-30',
  'zone': 'wildrose_peak',
  'cause': 'lost',
  'hazard': 'lost',
  'outcome': 'unknown',
  'temp_f': 30,
  'summary': '1988-04 | wildrose peak | lost | unknown | 30F'},
 {'case_id': '88-103',
  'incident_date': '1988-06-11',
  'zone': 'badwater_route',
  'cause': 'dehydration',
  'hazard': 'heat',
  'outcome': 'rescued',
  'temp_f': None,
  'summary': '1988-06 | badwater route | dehydration | rescued'},
 {'case_id': '91-323',
  'incident_date': '1991-07-21',
  'zone': 'badwater_route',
  'cause': 'heat',
  'hazard': 'heat',
  'outcome': 'fatal',
  'temp_f': 120,
  'summary': '1991-07 | badwater route | heat | fatal | 120F'},
 {'case_id': '94-454',
  'incident_date': '1994-08-06',
  'zone': 'telescope_trail',
  'cause': 'unknown',
  'hazard': 'other',
  'outcome': 'fatal',
  'temp_f': None,
  'summary': '1994-08 | telescope trail | unknown | fatal'},
 {'case_id': '97-776',
  'incident_date': '1998-01-04',
  'zone': 'jail_canyon',
  'cause': 'lost',
  'hazard': 'lost',
  'outcome': 'rescued',
  'temp_f': None,
  'summary': '1998-01 | jail canyon | lost | rescued'},
 {'case_id': '98-682',
  'incident_date': '1998-10-12',
  'zone': 'dantes_view_route',
  'cause': 'dehydration',
  'hazard': 'heat',
  'outcome': 'rescued',
  'temp_f': None,
  'summary': '1998-10 | dantes view route | dehydration | rescued'}]
# ===== END GENERATED: incidents =====

HAZARD_ALIASES = {"heat": "heat", "cold": "cold", "wind": "cold", "storm": "cold", "lost": "lost"}


def _month_distance(a, b):
    d = abs(a - b) % 12
    return min(d, 12 - d)


@tool()
def find_similar_incidents(hazard: str = "", month: int = 0, limit: int = 3) -> dict:
    """Find past Telescope-area incidents similar to the current hazard and season.

    Args:
        hazard: dominant_hazard from score_risk (heat, cold, wind, storm, lost or none).
        month: Month number 1-12; 0 means use the current month.
        limit: Maximum incidents to return (1-5).

    Returns:
        A dict with count and incidents (summary lines such as '1991-07 | badwater route | heat | fatal | 120F').
    """
    month = month or datetime.now().month
    want = HAZARD_ALIASES.get(hazard, "")
    scored = []
    for inc in INCIDENTS:
        s = 0.0
        if want and inc.get("hazard") == want:
            s += 2.0
        m = int(inc["incident_date"][5:7])
        s += {0: 1.5, 1: 1.0, 2: 0.3}.get(_month_distance(month, m), 0.0)
        scored.append((s, inc["incident_date"], inc))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top = [t[2]["summary"] for t in scored[:max(1, min(5, limit))] if t[0] > 0]
    return {"count": len(top), "incidents": top,
            "note": "Small historical sample; context only, not a prediction."}
