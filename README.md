# SafeXTrail starter kit - Telescope Peak risk score with explanations

Turns your data-flow diagram (boxes 2 and 3) into working pieces: scrapers, a trained-and-explained risk model,
six watsonx Orchestrate tools and two agents. Everything marked **tested** ran offline in the build environment;
anything that needs the internet or a live Orchestrate instance is marked **untested**.

```
safextrail/
  scrapers/        morning_report.py  incident_structuring.py  weather.py  nps_mortality.py
  risk_engine/     engine.py (scoring + explanation)  coefficients.json (expert priors until you train)
  notebooks/       safextrail_risk_model.ipynb  (data -> labels -> model -> export)
  orchestrate/     tools/ (6 files)  agents/ (2 YAMLs)  import_all.sh  requirements.txt
  server/          app.py  (your Flask MapShare app + timestamped points)
  scripts/         sync_tools.py  make_prior_coefficients.py
  tests/           test_pipeline.py + fixtures/      data/  incidents.json (seed from the fixture)
```

## Quick start
```bash
pip install -r requirements.txt
python -m tests.test_pipeline                         # offline checks (tested: all pass)
SAFEXTRAIL_DEMO=1 jupyter notebook notebooks/         # offline demo: synthetic weather, results are NOT science
jupyter notebook notebooks/                           # real run (needs internet)  -> writes real coefficients
bash orchestrate/import_all.sh                        # load tools + agents into Orchestrate (untested)
```

## How each step works

### 1. Scrape the incidents - `scrapers/morning_report.py`
Downloads the Death Valley Morning Report page once (cached in `data/raw/deva.html`), converts HTML to text, and splits it
into one record per report using a regex for `<date line> + <case id - park - headline>`. Everything up to the next such pair is the
narrative. *Tested on a fixture only* - on the first live run confirm the entry count is in the hundreds and adjust `ENTRY_RE` if not.
Edit `USER_AGENT` to identify yourself, and don't re-download on every run.

### 2. Structure and filter - `scrapers/incident_structuring.py`
1. Group follow-ups by case id (`91-323` Search + Followup become one incident).
2. **Drop suicides** (counted, text discarded) and non-hiking reports (vehicles, fires, crime).
3. Keep Telescope-area text: Telescope Peak, Mahogany Flat, Jail Canyon, Rogers Peak, Wildrose Peak, Dante's View.
4. Extract date (first "Month Day" in the narrative, else report date minus 3 days, flagged `low`), zone, cause (heat > dehydration > lost > cold > fall > medical),
   outcome, temperature, elevation. Output holds **derived fields only: no narratives, names or initials.**
Rule-based by design (deterministic, testable). To improve recall later, ask an LLM for the same JSON fields and compare against these rules on 20 hand-labelled rows.

### 3. Weather and context - `scrapers/weather.py`, `nps_mortality.py`
Open-Meteo gives daily history (training) and forecast (live) for two sites: the valley floor (Badwater, -86 m) and the summit (3,368 m), downscaled to real elevation.
Five features: `valley_feels_max_c`, `summit_feels_min_c`, `summit_gust_max_kph`, `summit_snow_cm`, `precip_mm`. Same definitions for history and forecast, so the model sees the same thing live.
NWS alerts add official warnings. The NPS mortality workbook is a **manual download** used only for cause-mix context (its loader is untested against the real file).

### 4. Label, train, validate - notebook steps 5-6
The reports only list incidents, so the denominator comes from the calendar: every day since 1989 is a row, `1` if an incident started that day.
The model learns what weather is over-represented on incident days: a **relative hazard, not a probability of injury**.
With few positives it has guard-rails: strong L2 regularization, physically-sensible sign constraints (drop and refit offenders), and shrinkage toward expert priors
(`w = n/(n+15)`). Validation is leave-one-incident-out, reported as the percentile where the held-out incident day lands (0.5 = chance).
The demo run showed why the guard-rails matter: unconstrained, a 5-incident fit made hotter weather look safer.

### 5. Score and explain - `risk_engine/engine.py`
`score = weather_points (0-55) + hiker_points (0-45)`; an SOS event forces 100.
* **Weather:** `contribution = coef * z * exposure`. Exposure encodes where the hiker is: heat counts fully in the valley and fades to 0.2 near the summit; cold/wind/snow do the reverse.
  The summed logit becomes a percentile of all historical days ("worse than 93% of days"), times 55. Points are split across factors by their share of the positive contributions, so the
  explanation is exact. A factor is only named if it is truly unusual (z >= 0.5); the rest rolls into "typical seasonal conditions".
* **Hiker state (rules, not trained):** overdue up to 20, stationary up to 15, off-route up to 8, low daylight up to 7.
* **Output:** score, level (LOW/MODERATE/HIGH/CRITICAL at 30/55/75), confidence (falls with stale GPS fix, missing weather, small training set), ranked factors with points and plain text,
  dominant hazard, recommended action, and a `model_note` that states honestly if coefficients are priors or synthetic.

### 6. Orchestrate tools - `orchestrate/tools/`
Each is a standalone file using the `@tool()` decorator; the docstring tells the agent when and how to call it.

| Tool | Role in your diagram | Notes |
|---|---|---|
| `get_latest_position` | box 3 -> 2, "live positions + trip plans" | Calls the Flask `/api/location`; computes minutes stationary, overdue, off-route, SOS |
| `get_forecast` | Weather Agent | Open-Meteo -> the 5 model features + daylight left |
| `get_alerts` | Weather Agent | NWS alerts |
| `score_risk` | Machine Learning + Trained Model | Engine and coefficients are **generated** into this file by `scripts/sync_tools.py` |
| `find_similar_incidents` | Trained Model (history) | Matches hazard + month to past incidents |
| `finalize_hiker_message` | dashed "Messages to hiker" path | Enforces 160 ASCII chars; always `requires_operator_approval` |

Because tools must be single files, **edit `risk_engine/`, then run `python scripts/sync_tools.py`**. A test proves the tool and engine give identical scores.

### 7. Agents - `orchestrate/agents/`
* `weather_agent` calls `get_forecast` + `get_alerts` and replies in a fixed `BRIEFING / ALERTS / FEATURES` layout.
* `risk_agent` is the supervisor: position -> weather_agent -> `score_risk` -> `find_similar_incidents` -> optional draft message. Its answer format is `SCORE / WHY / CONTEXT / ACTION / DRAFT MESSAGE (NOT SENT)`.
Instructions forbid invented numbers and require copying tool outputs verbatim. **If the agent mis-copies numbers**, give `risk_agent` the `get_forecast` tool directly and keep `weather_agent` just for the narrative.
The `llm:` line uses a Granite model as an example; swap for any model enabled in your environment. Use Bob (`wxo-builder` skill) to adjust these files and `wxo-analyzer` to audit them.

### 8. Server - `server/app.py`
Your MapShare Flask app with two changes: `/api/location` now returns `points` (time, lat, lon, elevation, velocity, event; needed for "minutes stationary"), and debug/host come from env vars
(`HOST=0.0.0.0` only if a container or tunnel must reach it). *Tested in demo mode with the sample KML.*

## Before this works end to end (untested pieces)
1. **Reachability:** Orchestrate cannot see `localhost`. Set `SAFEXTRAIL_API_URL` (Developer Edition: `http://host.docker.internal:5000`; cloud: a tunnel or deployed server). Prefer an Orchestrate *connection* over an env var in production.
2. **Real MapShare ID:** keep it out of git (your README already warns about this). Anyone with the MapShare link can see the hiker's live location.
3. **SOS wording:** `sos_active` loosely matches "sos"/"emergency" in the feed's event text. Check the real event labels from your device.
4. **Planned route:** fill `ROUTE_POINTS` in `get_latest_position.py` from a GPX file, or off-route is skipped (-1).
5. **Orchestrate setup:** install `ibm-watsonx-orchestrate`, activate an environment, then run `orchestrate/import_all.sh` (tools first, then agents). Check the ADK docs for exact environment commands.
6. **Live scrape and weather:** run the notebook live and hand-check the incident table against the source page.

## Honest limits to tell judges
Tiny incident sample; incident-only labels so the score is a relative hazard; reporting bias toward popular routes; rule-based extraction; hiker-state weights are expert-set, not learned.
The score is decision support with a human approving every dispatch and every message.

## REST API and Orchestrate integration (server/)
Setup: `cp .env.example server/.env`, fill in `WXO_API_KEY`, `WXO_INSTANCE_URL`, `WXO_RISK_AGENT_ID`, then `cd server && python app.py`.
Keys live only in `server/.env` (git-ignored) or your host's environment variables, and are read by `server/orchestrate_client.py`. Never put them in `index.html` or `console.js`.

| Route (`/api/v1`) | Purpose |
|---|---|
| `GET /health` | Orchestrate configured? demo mode? weather reachable? |
| `GET /adventurers` | Roster sorted by risk (fast path: local risk engine) |
| `GET /adventurers/<id>` | Position, telemetry, risk score + breakdown, track |
| `POST /adventurers/<id>/assess` | Calls the Orchestrate `risk_agent` (slow path), parsed into score / why / context / action / draft |
| `POST /agent/chat` | Free-form chat with `risk_agent`, keeps the thread |
| `GET /events` | Risk changes, overdue, stationary, queued actions |
| `POST /adventurers/<id>/actions/<dispatch\|message\|alert-sar>` | Needs `approved_by`; logged and queued, nothing is sent |

Tests: `python -m tests.test_api` (Orchestrate and Open-Meteo mocked). Not tested against a live Orchestrate instance.
