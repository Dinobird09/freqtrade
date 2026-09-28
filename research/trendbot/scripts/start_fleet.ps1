# Start the fleet dashboard (up to 10 bots); then open http://127.0.0.1:8050/
param([string]$Fleet = "fleet.json", [int]$Port = 8050)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..\..")
.\.venv\Scripts\python.exe -m research.trendbot.dashboard --fleet $Fleet --port $Port
