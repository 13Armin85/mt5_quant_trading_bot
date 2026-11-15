$ErrorActionPreference = 'Stop'
$botRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $botRoot
$botPython = Join-Path $botRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $botPython)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { & py -3 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or newer, then open Start.cmd again.' }
}
& $botPython -c "from importlib.util import find_spec; raise SystemExit(0 if all(find_spec(n) for n in ['MetaTrader5','pandas','pydantic_settings','yaml','sqlalchemy','loguru','httpx','tzdata']) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $botPython -m pip install --only-binary=:all: -r requirements-bridge.txt
    if ($LASTEXITCODE -ne 0) { throw 'Windows bridge dependencies could not be installed.' }
}
try { $engineType = & docker info --format '{{.OSType}}' 2>$null } catch { $engineType = '' }
if ($LASTEXITCODE -ne 0 -or $engineType -ne 'linux') {
    $desktopPaths = @((Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'), (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'))
    $desktopExe = $desktopPaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $desktopExe) { throw 'Install Docker Desktop, then open Start.cmd again.' }
    Start-Process -FilePath $desktopExe -WindowStyle Hidden
    $dockerDeadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        Start-Sleep -Seconds 2
        try { $engineType = & docker info --format '{{.OSType}}' 2>$null } catch { $engineType = '' }
        if ($LASTEXITCODE -eq 0 -and $engineType -eq 'linux') { break }
    } while ([DateTime]::UtcNow -lt $dockerDeadline)
    if ($engineType -ne 'linux') { throw 'Docker Linux Engine is not ready. Check Docker Desktop.' }
}
& $botPython -m scripts.portal_setup
if ($LASTEXITCODE -ne 0) { throw 'Portal setup failed; existing journals were preserved.' }
& $botPython -m core.account_portal --probe
if ($LASTEXITCODE -ne 0) {
    $portalProcess = Start-Process -FilePath $botPython -ArgumentList @('-m','core.account_portal') -WorkingDirectory $botRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $botRoot 'logs\portal.stdout.log') -RedirectStandardError (Join-Path $botRoot 'logs\portal.stderr.log')
    $portalReady = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 1
        if ($portalProcess.HasExited) { throw 'Account portal exited. Check logs\portal.stderr.log.' }
        & $botPython -m core.account_portal --probe
        if ($LASTEXITCODE -eq 0) { $portalReady = $true; break }
    }
    if (-not $portalReady) { throw 'Account portal did not become ready.' }
}
& docker compose build trader
if ($LASTEXITCODE -ne 0) { throw 'Docker image build failed.' }
& docker compose up -d --no-build dashboard
if ($LASTEXITCODE -ne 0) { throw 'Dashboard startup failed.' }
$dashboardReady = $false
$healthProbe = @'
import urllib.request
try:
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    response = client.open('http://127.0.0.1:8501/_stcore/health', timeout=3)
    raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
'@
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    & $botPython -c $healthProbe
    if ($LASTEXITCODE -eq 0) { $dashboardReady = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $dashboardReady) { throw 'Dashboard is not ready. Check docker compose logs dashboard.' }
Start-Process 'http://localhost:8501'
Write-Host 'Login page: http://localhost:8501 -- choose Demo or Real, then connect your MT5 account.'
Write-Host 'Stop everything with Stop.cmd. No code or .env edits are required.'
