#!/usr/bin/env bash
# Start the dashboard (with Start/Stop buttons) for bot.json; open http://127.0.0.1:8050/
set -euo pipefail
cd "$(dirname "$0")/../../.."          # repo root
exec .venv/bin/python -m research.trendbot.dashboard --settings "${1:-bot.json}" --port "${PORT:-8050}"
