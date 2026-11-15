#!/usr/bin/env bash
set -euo pipefail
app=/opt/noatech/app
install -d -o noatech -g noatech -m 0750 "$app/.docker" "$app/.docker/data" "$app/.docker/control" "$app/logs" "$app/reports"
chmod 2770 "$app/.docker/data" "$app/.docker/control" "$app/logs" "$app/reports"
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
NOA_PORTAL_ACCESS_FILE=/etc/noatech/portal-access.json
NOA_AUTO_LOGIN_FLAG=/opt/noatech/app/.docker/control/auto_login
MT5_PORTABLE=true
DATABASE_URL=sqlite:///.docker/data/trading_bot.db
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
ENV
printf 'NOA_SERVICE_GID=%s\n' "$(id -g noatech)" >> /etc/noatech/environment
chmod 0640 /etc/noatech/environment
chown root:noatech /etc/noatech/environment
cat > /etc/systemd/system/noatech-portal.service <<'UNIT'
[Unit]
Description=NOATECH account portal and MetaTrader bridge
Requires=noatech-display.service docker.service
After=noatech-display.service docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=noatech
Group=noatech
SupplementaryGroups=docker
WorkingDirectory=/opt/noatech/app
EnvironmentFile=/etc/noatech/environment
LoadCredentialEncrypted=mt5:/etc/noatech/mt5.cred
ExecStart=/opt/noatech/venv/bin/python -m core.account_portal
ExecStartPost=/opt/noatech/venv/bin/python -m scripts.server_session
Restart=on-failure
RestartSec=15
TimeoutStartSec=300
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=/var/lib/noatech /opt/noatech/app/.docker /opt/noatech/app/logs /opt/noatech/app/reports /tmp
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
User=noatech
Group=noatech
SupplementaryGroups=docker
WorkingDirectory=/opt/noatech/app
ExecStart=/usr/bin/docker compose -f compose.server.yaml up -d --no-build dashboard
EnvironmentFile=/etc/noatech/environment
ExecStop=/usr/bin/docker compose -f compose.server.yaml stop dashboard
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT
runuser -u noatech -- env HOME=/var/lib/noatech DATABASE_URL=sqlite:///.docker/data/trading_bot.db \
    /opt/noatech/venv/bin/python -m scripts.portal_setup
systemctl daemon-reload
systemctl enable noatech-portal noatech-dashboard
echo 'Service units prepared; credentials and journal must be ready before activation.'
