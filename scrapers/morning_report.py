"""
Step 1 - Scrape the NPS Morning Report incident archive for Death Valley.

What it does
------------
1. Downloads https://npshistory.com/morningreport/incidents/deva.htm ONCE and caches the
   raw HTML in data/raw/deva.html (so you never hammer the site while iterating).
2. Converts the HTML to plain text.
3. Splits the text into one record per report. Every report looks like:

       Thursday, January 8, 1998
       97-776 - Death Valley NP (CA/NV) - Search and Rescue
       <narrative paragraph(s)>

   so the splitter looks for  <date line> + <case id - park - headline>  and treats the text
   up to the next such pair as the narrative.

Run it:  python -m scrapers.morning_report            (uses cache if present)
         python -m scrapers.morning_report --refresh  (re-download)

NOTE: written against the page text as seen in a text/markdown view and tested on
tests/fixtures/deva_sample.txt only. On your first live run, open data/raw/deva.html and
confirm the entry count looks right (hundreds of entries). If the raw HTML lays dates and
headlines out differently, adjust ENTRY_RE only.
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://npshistory.com/morningreport/incidents/deva.htm"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw" / "deva.html"
ENTRIES_OUT = ROOT / "data" / "raw" / "entries.jsonl"
USER_AGENT = "SafeXTrail-hackathon/0.1 (research prototype; contact: you@example.com)"  # edit me

# date line  +  case id  +  headline.  Weekday is optional (old entries omit it).
ENTRY_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+)?"
    r"(?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})[ \t]*\n+[ \t]*"
    r"(?P<case>\d{2}-\d+[A-Z]?)\s+-\s+(?P<headline>[^\n]+)"
)


@dataclass
class Entry:
    report_date: str      # ISO date the report was filed
    case_id: str          # e.g. "97-776" (follow-ups reuse the id, sometimes with a letter)
    headline: str         # e.g. "Death Valley NP (CA/NV) - Search and Rescue"
    body: str             # narrative text


def fetch_html(refresh: bool = False, url: str = URL, cache: Path = CACHE) -> str:
    """Return page HTML, downloading only if the cache is missing (or refresh=True)."""
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8", errors="replace")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(resp.text, encoding="utf-8")
    return resp.text


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n")


def parse_entries(text: str) -> list:
    """Split plain text into Entry records, oldest first."""
    matches = list(ENTRY_RE.finditer(text))
    entries = []
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = re.sub(r"\s+", " ", text[m.end():body_end]).strip()
        try:
            d = datetime.strptime(m.group("date"), "%B %d, %Y").date()
        except ValueError:
            continue  # malformed date, skip
        entries.append(Entry(d.isoformat(), m.group("case"), m.group("headline").strip(), body))
    entries.sort(key=lambda e: e.report_date)
    return entries


def load_entries(refresh: bool = False) -> list:
    return parse_entries(html_to_text(fetch_html(refresh=refresh)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = ap.parse_args(argv)
    entries = load_entries(refresh=args.refresh)
    ENTRIES_OUT.parent.mkdir(parents=True, exist_ok=True)
    with ENTRIES_OUT.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e)) + "\n")
    print(f"Parsed {len(entries)} entries -> {ENTRIES_OUT}")
    if entries:
        print(f"Date range: {entries[0].report_date} .. {entries[-1].report_date}")


if __name__ == "__main__":
    sys.exit(main())
