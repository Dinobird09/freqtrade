# One-time setup on Windows (PowerShell): creates .venv in the repo root and installs ccxt.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..\..")
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ required')"
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r research\trendbot\requirements.txt
if (-not (Test-Path bot.json)) { Copy-Item research\trendbot\bot_settings.example.json bot.json }
if (-not (Test-Path .env)) { Copy-Item research\trendbot\.env.example .env }
Write-Host "Setup done. Edit bot.json (and .env for testnet/live), then run research\trendbot\scripts\start.ps1"
