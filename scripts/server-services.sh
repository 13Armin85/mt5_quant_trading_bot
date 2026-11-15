#!/usr/bin/env bash
set -euo pipefail
app=/opt/noatech/app
python3 - <<'PY'
import grp,os
from pathlib import Path
base=Path('/opt/noatech/app')
gid=grp.getgrnam('noatech').gr_gid
os.chown(base,0,gid)
os.chmod(base,0o750)
for directory,dirs,files in os.walk(base,followlinks=False):
    if Path(directory)==base:
        dirs[:]=[d for d in dirs if d not in {'.docker','logs','reports'}]
    for name in [*dirs,*files]:
        path=Path(directory)/name
        if path.is_symlink():
            raise SystemExit('Application source must not contain symlinks')
        os.chown(path,0,gid)
        os.chmod(path,0o750 if path.is_dir() else 0o640)
PY
install -d -o root -g noatech -m 0750 /etc/noatech "$app/.docker"
install -d -o root -g root -m 0700 /etc/noatech/docker-cli
install -d -o noatech -g noatech -m 0750 /var/lib/noatech/state "$app/.docker/data" "$app/.docker/control" "$app/logs" "$app/reports"
chmod 2770 "$app/.docker/data" "$app/.docker/control" "$app/logs" "$app/reports"
python3 - <<'PY'
import grp,os,secrets
from pathlib import Path
for name in ('bridge','portal'):
    path=Path('/etc/noatech')/(name+'.token')
    if path.is_symlink():
        raise SystemExit('Secret file must not be a symlink')
    if not path.exists():
        with path.open('x') as handle:
            os.chmod(path,0o600)
            handle.write(secrets.token_urlsafe(48))
    os.chown(path,0,grp.getgrnam('noatech').gr_gid)
    os.chmod(path,0o640)
PY
if id -nG noatech | tr ' ' '\n' | grep -qx docker; then
    gpasswd -d noatech docker
fi
cat > /etc/noatech/environment <<'ENV'
HOME=/var/lib/noatech
DISPLAY=:99
XAUTHORITY=/var/lib/noatech/.Xauthority
WINEPREFIX=/var/lib/noatech/wine
WINEDEBUG=-all
WINEDLLOVERRIDES=ucrtbase=n,b;winedbg.exe=d
PYTHONUTF8=1
PYTHONDONTWRITEBYTECODE=1
NOA_MT5_RUNTIME=wine
NOA_WINE_PYTHON=/var/lib/noatech/wine/drive_c/Python312/python.exe
NOA_COMPOSE_FILE=/opt/noatech/app/compose.server.yaml
NOA_HOST_STATE_DIR=/var/lib/noatech/state
NOA_MT5_STARTUP_DIR=/run/noatech-terminal
NOA_DOCKER_CONTROL_SOCKET=/run/noatech-docker/control.sock
MT5_BRIDGE_TOKEN_FILE=/etc/noatech/bridge.token
ACCOUNT_PORTAL_TOKEN_FILE=/etc/noatech/portal.token
RUNTIME_LOCK_PATH=/var/lib/noatech/state/mt5.lock
NOA_PORTAL_ACCESS_FILE=/etc/noatech/portal-access.json
NOA_AUTO_LOGIN_FLAG=/opt/noatech/app/.docker/control/auto_login
NOA_DEMO_TRADING_FLAG=/etc/noatech/demo-trading.enabled
MT5_PORTABLE=true
DATABASE_URL=sqlite:///.docker/data/trading_bot.db
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
ENV
printf 'NOA_SERVICE_GID=%s\n' "$(id -g noatech)" >> /etc/noatech/environment
if [[ -x /opt/wine-devel/bin/wine ]]; then
    printf 'NOA_WINE=/opt/wine-devel/bin/wine\n' >> /etc/noatech/environment
fi
chmod 0640 /etc/noatech/environment
chown root:noatech /etc/noatech/environment
cat > /etc/systemd/system/noatech-portal.service <<'UNIT'
[Unit]
Description=NOATECH account portal and MetaTrader bridge
Requires=noatech-display.service noatech-docker.service
After=noatech-display.service noatech-docker.service network-online.target
Wants=network-online.target

[Service]
Type=exec
User=noatech
Group=noatech
RuntimeDirectory=noatech-terminal
RuntimeDirectoryMode=0700
WorkingDirectory=/opt/noatech/app
EnvironmentFile=/etc/noatech/environment
LoadCredentialEncrypted=mt5:/etc/noatech/mt5.cred
ExecStart=/opt/noatech/venv/bin/python -m core.account_portal
ExecStartPost=/opt/noatech/venv/bin/python -m scripts.server_session
ExecStop=/opt/noatech/venv/bin/python -m scripts.server_stop
Restart=on-failure
RestartSec=15
TimeoutStartSec=300
TimeoutStopSec=240
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=/var/lib/noatech /run/noatech-terminal /opt/noatech/app/.docker/data /opt/noatech/app/.docker/control /opt/noatech/app/logs /opt/noatech/app/reports /tmp
LimitCORE=0
TasksMax=512

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/noatech-dashboard.service <<'UNIT'
[Unit]
Description=NOATECH browser workspace
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
Group=root
WorkingDirectory=/opt/noatech/app
ExecStart=/usr/bin/docker compose -f compose.server.yaml up -d --no-build dashboard
EnvironmentFile=/etc/noatech/environment
Environment=DOCKER_CONFIG=/etc/noatech/docker-cli
ExecStop=/usr/bin/docker compose -f compose.server.yaml stop dashboard
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/noatech-docker.service <<'UNIT'
[Unit]
Description=NOATECH restricted Docker controller
Requires=docker.service
After=docker.service

[Service]
User=root
Group=noatech
WorkingDirectory=/opt/noatech/app
RuntimeDirectory=noatech-docker
RuntimeDirectoryMode=0750
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m scripts.server_docker
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/run/noatech-docker /tmp
UMask=0077
LimitCORE=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable noatech-docker noatech-portal noatech-dashboard
echo 'Service units prepared; credentials and journal must be ready before activation.'
