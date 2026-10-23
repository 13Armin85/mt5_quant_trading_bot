$ErrorActionPreference = 'Stop'
$botRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $botRoot
$botPython = Join-Path $botRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $botPython)) {
    throw 'Install the Windows environment first: py -3.12 -m venv .venv; then .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}
try { $engineType = & docker info --format '{{.OSType}}' 2>$null }
catch { throw 'Start Docker Desktop with Linux containers / WSL 2, then run Start-Live again.' }
if ($LASTEXITCODE -ne 0 -or $engineType -ne 'linux') {
    throw 'Start Docker Desktop and select Linux containers / WSL 2, then run Start-Live again.'
}
& $botPython -m scripts.docker_setup
if ($LASTEXITCODE -ne 0) { throw 'Docker setup failed. See the message above.' }
& $botPython -m scripts.docker_setup --probe
if ($LASTEXITCODE -ne 0) {
    $bridgeProcess = Start-Process -FilePath $botPython -ArgumentList @('-m', 'core.mt5_bridge', '--allow-live') -WorkingDirectory $botRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $botRoot 'logs\bridge.stdout.log') -RedirectStandardError (Join-Path $botRoot 'logs\bridge.stderr.log')
    # The bridge initializes MT5 on first request. Probe timeouts are three seconds.
    $bridgeReady = $false
    $bridgeDeadline = [DateTime]::UtcNow.AddSeconds(120)
    while ([DateTime]::UtcNow -lt $bridgeDeadline) {
        if ($bridgeProcess.HasExited) { throw 'Windows bridge exited. Check logs\bridge.stderr.log; another native bot may hold .bot.lock.' }
        Start-Sleep -Seconds 3
        & $botPython -m scripts.docker_setup --probe
        if ($LASTEXITCODE -eq 0) { $bridgeReady = $true; break }
    }
    if (-not $bridgeReady) { throw 'MT5 bridge is not ready. Check MT5 login and logs\bridge.stderr.log.' }
}
& docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid compose configuration.' }
& docker compose up -d --build trader
if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed; Windows bridge remains available for retry.' }
Write-Host 'LIVE container started. Orders require a qualified signal, news data and MT5 permissions.'
Write-Host 'View: docker compose logs -f trader'
Write-Host 'Stop trading engine: docker compose stop trader'
