#!/usr/bin/env bash
# Ubuntu 26.04's packaged Wine 10 cannot start this MT5 build. Use WineHQ's
# signed packages for this release; do not patch the trading terminal binary.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 26.04 ]]; then
    echo 'This WineHQ provisioning script is for Ubuntu 26.04 only.' >&2
    exit 1
fi
install -d -m 0755 /etc/apt/keyrings
curl -fL --retry 3 https://dl.winehq.org/wine-builds/winehq.key -o /etc/apt/keyrings/winehq-archive.gpg
chmod 0644 /etc/apt/keyrings/winehq-archive.gpg
curl -fL --retry 3 https://dl.winehq.org/wine-builds/ubuntu/dists/resolute/winehq-resolute.sources -o /etc/apt/sources.list.d/winehq-resolute.sources
python3 - <<'PY'
from pathlib import Path
key=Path('/etc/apt/keyrings/winehq-archive.gpg')
if key.read_bytes().startswith(b'-----BEGIN PGP'):
    key.rename(key.with_suffix('.asc'))
    key=key.with_suffix('.asc')
source=Path('/etc/apt/sources.list.d/winehq-resolute.sources')
source.write_text(source.read_text().replace('/etc/apt/keyrings/winehq-archive.key',str(key)))
PY
apt-get update
apt-get install -y --no-install-recommends winehq-devel
wine --version
