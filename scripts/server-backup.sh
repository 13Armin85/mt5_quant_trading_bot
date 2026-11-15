#!/usr/bin/env bash
set -euo pipefail
test -f /etc/noatech/backup-recipient.pem
install -d -o root -g root -m 0700 /var/backups/noatech
cat > /etc/systemd/system/noatech-backup.service <<'UNIT'
[Unit]
Description=NOA owner-encrypted recovery snapshot
After=docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/noatech/app
ExecStart=/usr/bin/python3 -m scripts.server_backup
UMask=0077
Environment=PYTHONDONTWRITEBYTECODE=1
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/run /var/backups/noatech
MemoryMax=128M
TasksMax=16
TimeoutStartSec=180
LimitCORE=0
UNIT
cat > /etc/systemd/system/noatech-backup.timer <<'UNIT'
[Unit]
Description=Daily NOA encrypted recovery snapshot

[Timer]
OnCalendar=*-*-* 02:30:00 UTC
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now noatech-backup.timer
