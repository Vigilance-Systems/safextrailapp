"""
Step 3b - NPS mortality data (context for cause/outcome mix; NOT used as model labels).

The page https://www.nps.gov/aboutus/mortality-data.htm links to a downloadable Excel workbook.
Download it by hand (browser) into data/raw/ - it is a file download, not something to scrape.

Column names change between releases, so this loader discovers them by keyword and prints what it
found. If a guess is wrong it tells you the real columns so you can fix COLUMN_HINTS.
NOT tested against the real workbook (no network in the build environment).

Important: exclude intentional deaths (suicide/homicide) from hiker-risk analysis.
"""
from pathlib import Path
import pandas as pd

COLUMN_HINTS = {
    "park": ("park",),
    "date": ("date",),
    "cause": ("cause",),
    "intent": ("intent",),
}


def _find(cols, keys):
    for c in cols:
        if any(k in c.lower() for k in keys):
            return c
    return None


def load(path) -> pd.DataFrame:
    df = pd.read_excel(Path(path))
    found = {k: _find(df.columns, v) for k, v in COLUMN_HINTS.items()}
    print("Columns found:", found)
    if not found["park"]:
        raise ValueError(f"Could not find a park column. Real columns: {list(df.columns)}")
    return df


def death_valley_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Death Valley rows, unintentional only, counted by cause."""
    park = _find(df.columns, COLUMN_HINTS["park"])
    intent = _find(df.columns, COLUMN_HINTS["intent"])
    cause = _find(df.columns, COLUMN_HINTS["cause"])
    dv = df[df[park].astype(str).str.contains("Death Valley", case=False, na=False)]
    if intent:
        dv = dv[dv[intent].astype(str).str.contains("unintentional", case=False, na=False)]
    return dv[cause].value_counts().rename("deaths").to_frame() if cause else dv
