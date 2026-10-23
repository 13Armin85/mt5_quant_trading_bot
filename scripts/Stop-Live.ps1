$ErrorActionPreference = 'Stop'
$botRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $botRoot
& docker compose stop trader dashboard
if ($LASTEXITCODE -ne 0) { throw 'Could not stop the trader. Bridge left running.' }
& (Join-Path $botRoot '.venv\Scripts\python.exe') -m scripts.docker_setup --stop-bridge
if ($LASTEXITCODE -ne 0) { throw 'Bridge is still active; check its lease and retry after five minutes if the trader crashed.' }
Write-Host 'Trader and bridge stopped. Existing broker positions retain their SL/TP.'
