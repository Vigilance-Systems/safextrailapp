"""
Step 2 - Turn raw Morning Report narratives into structured, filtered incident records.

Pipeline (all rule-based so it is deterministic and unit-testable):

  entries -> group follow-ups by case id -> drop non-hiking incidents ->
  drop sensitive (suicide) incidents -> keep Telescope-area incidents ->
  extract fields (date, zone, cause, outcome, temperature, elevation)

Why filter like this?
  * The archive is mostly vehicle crashes, fires, drug cases, etc. They say nothing about hiker risk.
  * Suicides are a different mechanism and a sensitive one; they do not belong in a hiker-risk model.
    We count them (so you can report how much was excluded) but never keep their text.
  * We keep only DERIVED fields in the output - no narratives, no names or initials.

Upgrade path: the regex rules below are the baseline. For better recall, send each narrative to an
LLM with a JSON schema (same field names) and compare against these rules on a hand-labelled sample.
"""
import re
from collections import OrderedDict
from datetime import date, timedelta
from typing import Optional

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b")

HIKER_TYPE_RE = re.compile(r"search|rescue|fatalit|body|heat|exposure|missing|overdue", re.I)
NON_HIKER_RE = re.compile(r"MVA|aircraft|plane|airplane|fire|drug|assault|theft|arrest|explosive|"
                          r"armed|poach|spill|earthquake|storm|flood|crash", re.I)
SENSITIVE_RE = re.compile(r"suicid|self-inflicted|gunshot|despondent|took (his|her) own life", re.I)

# Telescope Peak area. Edit this pattern to widen or narrow the study area.
AREA_RE = re.compile(r"Telescope|Mahogany Flat|Jail Canyon|Rogers Peak|Wildrose Peak|Dante'?s View", re.I)

CAUSE_RULES = [  # priority order: first match wins
    ("heat",        re.compile(r"heat stroke|heat exhaustion|\bheat\b", re.I)),
    ("dehydration", re.compile(r"dehydrat|run out of water|ran out of water|out of water", re.I)),
    ("lost",        re.compile(r"\blost\b|disoriented|stranded|overdue|failed to return|wandered", re.I)),
    ("cold",        re.compile(r"hypotherm|\bsnow|freezing|frost|below 30", re.I)),
    ("fall",        re.compile(r"\bfell\b|\bfall\b|\bslip", re.I)),
    ("medical",     re.compile(r"heart|cardiac", re.I)),
]
FATAL_RE = re.compile(r"found the body|body (?:had been |was )?found|found dead|\bdied\b|\bkilled\b|"
                      r"fatal|cause of death|deceased", re.I)
RESCUED_RE = re.compile(r"rescued|medevac|flown out|was located|were located|located and|helicopter", re.I)
UNRESOLVED_RE = re.compile(r"no sign|no trace|search was (?:called off|suspended)|still missing", re.I)

HAZARD_OF_CAUSE = {"heat": "heat", "dehydration": "heat", "cold": "cold", "lost": "lost"}


def base_case_id(case_id: str) -> str:
    """'87-217A' -> '87-217' so follow-ups group with the original report."""
    m = re.match(r"(\d{2}-\d+)", case_id)
    return m.group(1) if m else case_id


def _zone(text: str) -> str:
    if re.search(r"Dante", text, re.I):
        return "dantes_view_route"
    if re.search(r"Bad ?water", text, re.I) and re.search(r"Telescope", text, re.I):
        return "badwater_route"
    if re.search(r"Jail Canyon", text, re.I):
        return "jail_canyon"
    if re.search(r"Mahogany", text, re.I):
        return "mahogany_flat"
    if re.search(r"Rogers Peak", text, re.I):
        return "rogers_peak"
    if re.search(r"Wildrose Peak", text, re.I):
        return "wildrose_peak"
    return "telescope_trail"


def _cause(text: str, temp_f: Optional[int]) -> str:
    for name, rx in CAUSE_RULES:
        if rx.search(text):
            return name
    if temp_f is not None and temp_f >= 100:
        return "heat"
    return "unknown"


def _outcome(text: str) -> str:
    if FATAL_RE.search(text):
        return "fatal"
    if RESCUED_RE.search(text):
        return "rescued"
    if UNRESOLVED_RE.search(text):
        return "unresolved"
    return "unknown"


def _temp_f(text: str) -> Optional[int]:
    vals = [int(v) for v in re.findall(r"(\d{2,3})\s*degrees", text)]
    vals = [v for v in vals if -20 <= v <= 140]
    return max(vals) if vals else None


def _elevation_ft(text: str) -> Optional[int]:
    vals = [int(v.replace(",", "")) for v in re.findall(r"([\d,]{4,6})(?:-foot| feet| ft)", text)]
    vals = [v for v in vals if 0 < v < 15000]
    return max(vals) if vals else None


def _incident_date(text: str, report: date):
    """First 'Month Day' mentioned in the narrative = best guess for when it started.
    Returns (date, confidence). Falls back to report date minus 3 days (low confidence)."""
    for mon, day in MONTH_RE.findall(text):
        try:
            cand = date(report.year, MONTHS.index(mon) + 1, int(day))
        except ValueError:
            continue
        if cand > report:                       # e.g. a December event reported in January
            cand = date(report.year - 1, cand.month, cand.day)
        if 0 <= (report - cand).days <= 60:
            return cand, "narrative"
    return report - timedelta(days=3), "low"


def _summary(rec: dict) -> str:
    bits = [rec["incident_date"][:7], rec["zone"].replace("_", " "), rec["cause"], rec["outcome"]]
    if rec.get("temp_f"):
        bits.append(f"{rec['temp_f']}F")
    return " | ".join(bits)


def structure(entries) -> dict:
    """entries: list of scrapers.morning_report.Entry (or dicts). Returns
    {"incidents": [...], "stats": {...}} - incidents contain only derived fields."""
    groups = OrderedDict()                       # base case id -> list of entries
    for e in entries:
        e = e if isinstance(e, dict) else e.__dict__
        groups.setdefault(base_case_id(e["case_id"]), []).append(e)

    stats = {"entries": len(entries), "cases": len(groups), "non_hiker": 0,
             "sensitive_excluded": 0, "outside_area": 0, "kept": 0}
    incidents = []
    for cid, items in groups.items():
        headline = " ".join(i["headline"] for i in items)
        text = " ".join(i["body"] for i in items)
        if SENSITIVE_RE.search(headline + " " + text):
            stats["sensitive_excluded"] += 1     # counted, text discarded
            continue
        if NON_HIKER_RE.search(headline) or not HIKER_TYPE_RE.search(headline):
            stats["non_hiker"] += 1
            continue
        if not AREA_RE.search(text):
            stats["outside_area"] += 1
            continue
        report = date.fromisoformat(items[0]["report_date"])
        inc_date, conf = _incident_date(text, report)
        temp = _temp_f(text)
        cause = _cause(text, temp)
        rec = {
            "case_id": cid,
            "report_date": report.isoformat(),
            "incident_date": inc_date.isoformat(),
            "date_confidence": conf,
            "zone": _zone(text),
            "cause": cause,
            "hazard": HAZARD_OF_CAUSE.get(cause, "other"),
            "outcome": _outcome(text),
            "temp_f": temp,
            "elevation_ft": _elevation_ft(text),
        }
        rec["summary"] = _summary(rec)
        incidents.append(rec)
        stats["kept"] += 1
    return {"incidents": incidents, "stats": stats}
