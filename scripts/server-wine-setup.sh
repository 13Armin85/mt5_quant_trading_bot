#!/usr/bin/env bash
set -euo pipefail
prefix=/var/lib/noatech/wine
app=/opt/noatech/app
export DEBIAN_FRONTEND=noninteractive
export XAUTHORITY=/var/lib/noatech/.Xauthority
export WINEDLLOVERRIDES='ucrtbase=n,b;winedbg.exe=d'
echo '[1/5] Preparing the Windows environment'
runuser -u noatech -- env HOME=/var/lib/noatech WINEPREFIX="$prefix" DISPLAY=:99 WINEDEBUG=-all wineboot -u
install -d -o noatech -g noatech "$prefix/drive_c/Python312" "$prefix/drive_c/Program Files/MetaTrader 5"
echo '[2/5] Installing official Windows Python 3.12'
curl --fail --location --retry 3 https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip -o /tmp/noatech-python312.zip
unzip -o -q /tmp/noatech-python312.zip -d "$prefix/drive_c/Python312"
cat > "$prefix/drive_c/Python312/python312._pth" <<'PATHS'
python312.zip
.
Lib\site-packages
Z:\opt\noatech\app
import site
PATHS
chown -R noatech:noatech "$prefix/drive_c/Python312"
curl --fail --location --retry 3 https://bootstrap.pypa.io/get-pip.py -o /tmp/noatech-get-pip.py
echo '[3/5] Preparing native MT5 SDK dependencies'
runuser -u noatech -- env HOME=/var/lib/noatech WINEPREFIX="$prefix" DISPLAY=:99 WINEDEBUG=-all PYTHONUTF8=1 \
    wine "$prefix/drive_c/Python312/python.exe" 'Z:\tmp\noatech-get-pip.py' --no-warn-script-location
cd "$app"
runuser -u noatech -- env HOME=/var/lib/noatech WINEPREFIX="$prefix" DISPLAY=:99 WINEDEBUG=-all PYTHONUTF8=1 \
    wine "$prefix/drive_c/Python312/python.exe" -m pip install --only-binary=:all: -r 'Z:\opt\noatech\app\requirements-bridge.txt'
echo '[4/5] Preparing the Linux account controller and certificate client'
python3 -m venv /opt/noatech/venv
/opt/noatech/venv/bin/python -m pip install --only-binary=:all: -r "$app/requirements-bridge.txt" 'certbot>=5.4'
echo '[5/5] Checking native imports'
runuser -u noatech -- env HOME=/var/lib/noatech WINEPREFIX="$prefix" DISPLAY=:99 WINEDEBUG=-all PYTHONUTF8=1 \
    wine "$prefix/drive_c/Python312/python.exe" -c 'import MetaTrader5,pandas,numpy;print("Native MT5 SDK ready")'
echo 'Wine and Python setup complete'
