"""
Copy the tested engine, coefficients and incident list INTO the Orchestrate tool files.

Orchestrate Python tools are imported as standalone files, so they cannot `import risk_engine`.
Instead each tool contains marked blocks that this script regenerates:

  orchestrate/tools/score_risk.py            <- risk_engine/engine.py + risk_engine/coefficients.json
  orchestrate/tools/find_similar_incidents.py <- data/incidents.json

Run after retraining:  python scripts/sync_tools.py
"""
import json
import pprint
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_block(path: Path, name: str, content: str):
    text = path.read_text(encoding="utf-8")
    pat = re.compile(r"(# ===== BEGIN GENERATED: %s[^\n]*\n).*?(# ===== END GENERATED: %s =====)" % (re.escape(name), re.escape(name)), re.S)
    if not pat.search(text):
        raise SystemExit("markers for %r not found in %s" % (name, path))
    path.write_text(pat.sub(lambda m: m.group(1) + content.rstrip() + "\n" + m.group(2), text), encoding="utf-8")


def main():
    engine = (ROOT / "risk_engine" / "engine.py").read_text(encoding="utf-8")
    engine = re.sub(r'^""".*?"""\n', "", engine, count=1, flags=re.S)       # drop module docstring
    coeffs = json.loads((ROOT / "risk_engine" / "coefficients.json").read_text())
    tool = ROOT / "orchestrate" / "tools" / "score_risk.py"
    replace_block(tool, "engine", engine)
    replace_block(tool, "coefficients", "COEFFS = " + pprint.pformat(coeffs, width=110, sort_dicts=False))

    inc_path = ROOT / "data" / "incidents.json"
    incidents = json.loads(inc_path.read_text())
    keep = ("case_id", "incident_date", "zone", "cause", "hazard", "outcome", "temp_f", "summary")
    slim = [{k: i.get(k) for k in keep} for i in incidents]
    replace_block(ROOT / "orchestrate" / "tools" / "find_similar_incidents.py", "incidents",
                  "INCIDENTS = " + pprint.pformat(slim, width=110, sort_dicts=False))
    print("synced: %d coefficients block, %d incidents" % (len(coeffs["coefs"]), len(slim)))


if __name__ == "__main__":
    main()
