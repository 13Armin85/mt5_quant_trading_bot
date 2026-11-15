$ErrorActionPreference = 'Stop'
$botRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $botRoot
$botPython = Join-Path $botRoot '.venv\Scripts\python.exe'
& $botPython -m core.account_portal --probe
if ($LASTEXITCODE -eq 0) {
    & $botPython -m core.account_portal --stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop account portal; inspect the running trader before retrying.' }
} else {
    & docker compose stop trader
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the trading engine.' }
}
& docker compose stop dashboard
if ($LASTEXITCODE -ne 0) { throw 'Could not stop dashboard.' }
Write-Host 'NoaTech stopped. Broker positions retain their SL/TP.'
