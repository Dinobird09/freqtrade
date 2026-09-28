#!/usr/bin/env bash
# Start the fleet dashboard (up to 10 bots) for fleet.json; open http://127.0.0.1:8050/
set -euo pipefail
cd "$(dirname "$0")/../../.."          # repo root
exec .venv/bin/python -m research.trendbot.dashboard --fleet "${1:-fleet.json}" --port "${PORT:-8050}"
