# Start the dashboard (with Start/Stop buttons); then open http://127.0.0.1:8050/
param([string]$Settings = "bot.json", [int]$Port = 8050)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..\..")
.\.venv\Scripts\python.exe -m research.trendbot.dashboard --settings $Settings --port $Port
