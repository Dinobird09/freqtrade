#!/usr/bin/env bash
# One-time setup on macOS / Linux: creates .venv in the repo root and installs ccxt.
set -euo pipefail
cd "$(dirname "$0")/../../.."          # repo root
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required")'
"$PY" -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r research/trendbot/requirements.txt
[ -f bot.json ] || cp research/trendbot/bot_settings.example.json bot.json
[ -f .env ] || cp research/trendbot/.env.example .env
echo "Setup done. Edit bot.json (and .env for testnet/live), then run:"
echo "  research/trendbot/scripts/start.sh"
