#!/usr/bin/env bash
# Import SafeXTrail tools and agents into watsonx Orchestrate.
# Prereqs: pip install --upgrade ibm-watsonx-orchestrate ; an active environment
# (Developer Edition or cloud) - see the ADK docs for `orchestrate env` setup.
# Order matters: tools first, then the collaborator agent, then the supervisor agent.
set -euo pipefail
cd "$(dirname "$0")"

for t in get_forecast get_alerts get_latest_position score_risk find_similar_incidents finalize_hiker_message; do
  orchestrate tools import -k python -f "tools/${t}.py" -r requirements.txt
done

orchestrate agents import -f agents/weather_agent.yaml
orchestrate agents import -f agents/risk_agent.yaml
echo "Done. Open the Orchestrate chat, pick risk_agent, and ask: 'Assess the hiker, expected back at 23:30 UTC.'"
