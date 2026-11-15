#!/usr/bin/env bash
# Host prerequisites for NOATECH on Ubuntu. Credentials are supplied separately.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo '[1/5] Installing Docker, Wine, a virtual display and the web proxy'
apt-get update
apt-get install -y --no-install-recommends docker.io docker-compose-v2 nginx wine wine64 xvfb xauth xdotool fluxbox scrot python3-venv unzip curl ca-certificates fonts-dejavu-core
echo '[2/5] Creating the application service account and directories'
if ! id noatech >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir /var/lib/noatech --shell /usr/sbin/nologin noatech
fi
usermod -aG docker noatech
install -d -o noatech -g noatech -m 0750 /opt/noatech/app /var/lib/noatech /var/lib/noatech/wine
install -d -o root -g noatech -m 0750 /etc/noatech
install -d -m 0755 /var/www/noatech-acme
echo '[3/5] Providing swap capacity for the MT5 desktop and analysis engine'
if ! swapon --show=NAME --noheadings | grep -q .; then
    if [ ! -e /swap-noatech ]; then
        fallocate -l 4G /swap-noatech
        chmod 0600 /swap-noatech
        mkswap /swap-noatech
    fi
    swapon /swap-noatech
    if ! grep -q '^/swap-noatech ' /etc/fstab; then
        printf '/swap-noatech none swap sw 0 0\n' >> /etc/fstab
    fi
fi
echo '[4/5] Enabling Docker and the local virtual display'
systemctl enable --now docker
cat > /etc/systemd/system/noatech-display.service <<'UNIT'
[Unit]
Description=NOATECH MetaTrader virtual display
After=network.target

[Service]
User=noatech
Group=noatech
Environment=HOME=/var/lib/noatech
Environment=XAUTHORITY=/var/lib/noatech/.Xauthority
ExecStartPre=/bin/sh -c 'umask 077; touch /var/lib/noatech/.Xauthority; /usr/bin/xauth -f /var/lib/noatech/.Xauthority add :99 . "$(/usr/bin/mcookie)"'
ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -auth /var/lib/noatech/.Xauthority
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/noatech /tmp
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now noatech-display
echo '[5/5] Host prerequisites ready'
wine --version
docker compose version
free -h
grep -m1 -o 'avx2\|avx' /proc/cpuinfo || true
