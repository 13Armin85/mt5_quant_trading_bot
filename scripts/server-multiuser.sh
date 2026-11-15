#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 0
app=/opt/noatech/app
id noagate >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/empty --shell /usr/sbin/nologin noagate
getent group noactrl >/dev/null || groupadd --system noactrl
usermod -a -G noactrl noagate
# Identities are allocated by this administrator installer, never by the live
# provisioning daemon. Its sandbox can therefore keep passwd/shadow read-only.
for slot in $(seq 1 2); do
    id "noaworker$slot" >/dev/null 2>&1 || useradd --system --user-group --home-dir "/var/lib/noatech-slots/$slot" --shell /usr/sbin/nologin "noaworker$slot"
    usermod -a -G noactrl "noaworker$slot"
done
# The shared worker source is an explicit allowlist; it never contains .env,
# credentials, private keys, account journals, logs, models or Docker controls.
python3 - <<'PY'
import grp,json,os,pwd,secrets,shutil
from pathlib import Path
from hashlib import sha256
app=Path('/opt/noatech/app'); code=Path('/opt/noatech/tenant-app')
os.chmod('/opt/noatech',0o711)
code.mkdir(mode=0o755,exist_ok=True)
for name in ('core','scripts','monitoring','database','utils','config'):
    shutil.copytree(app/name,code/name,dirs_exist_ok=True,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copyfile(app/'config.py',code/'config.py')
for directory,dirs,files in os.walk(code):
    for name in dirs:
        if (Path(directory)/name).is_symlink():raise SystemExit('Worker directories must not contain symlinks')
    os.chown(directory,0,0);os.chmod(directory,0o755)
    for name in files:
        path=Path(directory)/name
        if path.is_symlink():raise SystemExit('Worker source must not contain symlinks')
        os.chown(path,0,0);os.chmod(path,0o644)
template=Path('/opt/noatech/tenant-template')
if not template.exists():
    template.mkdir(mode=0o700)
    shutil.copytree('/var/lib/noatech/wine/drive_c/Python312',template/'Python312',symlinks=True)
    terminal=template/'MetaTrader5';(terminal/'Config').mkdir(parents=True)
    for name in ('terminal64.exe','Config/servers.dat'):
        shutil.copyfile(Path('/var/lib/noatech/wine/drive_c/Program Files/MetaTrader 5')/name,terminal/name)
    for directory,dirs,files in os.walk(template):
        for name in dirs:
            if (Path(directory)/name).is_symlink():raise SystemExit('Template directories must not contain symlinks')
        os.chown(directory,0,0);os.chmod(directory,0o755)
        for name in files:
            path=Path(directory)/name
            if path.is_symlink():raise SystemExit('Template must not contain symlinks')
            os.chown(path,0,0);os.chmod(path,0o644)
    os.chmod(template,0o755)
gateway=pwd.getpwnam('noagate')
for directory,mode in [('/etc/noatech/tenants',0o700),('/var/lib/noatech-tenants',0o711),
    ('/etc/noatech-worker-policies',0o711),('/etc/noatech-gateway',0o750)]:
    path=Path(directory);path.mkdir(exist_ok=True);os.chmod(path,mode)
os.chown('/etc/noatech-gateway',0,gateway.pw_gid)
token=Path('/etc/noatech-gateway/token')
if not token.exists():token.write_text(secrets.token_urlsafe(48))
os.chown(token,0,gateway.pw_gid);os.chmod(token,0o640)
policy=Path('/etc/noatech/tenants-policy.json')
if not policy.exists():
    policy.write_text(json.dumps({'max_accounts':2,'minimum_available_mb':768,'public_registration':True,'real_trading':False}))
    os.chmod(policy,0o600)
owner=json.loads(Path('/etc/noatech/portal-access.json').read_text())
key=sha256(f"{owner['server']}:{owner['login']}".encode()).hexdigest()[:24]
private=Path('/etc/noatech/tenants')/key;private.mkdir(mode=0o700,exist_ok=True)
if not (private/'record.json').exists():
    user=pwd.getpwnam('noatech')
    record={'key':key,'slot':0,'login':owner['login'],'server':owner['server'],'account_type':owner['account_type'],
        'uid':user.pw_uid,'gid':user.pw_gid,'username':'noatech','home':'/var/lib/noatech','work':str(app),
        'portal_token':'/etc/noatech/portal.token','bridge_token':'/etc/noatech/bridge.token',
        'credential':'/etc/noatech/mt5.cred','portal_port':18766,'bridge_port':18765,
        'unit':'noatech-portal.service','verified':True,'legacy':True}
    (private/'record.json').write_text(json.dumps(record));os.chmod(private/'record.json',0o600)
environment=Path('/etc/noatech/environment')
lines=[line for line in environment.read_text().splitlines() if not line.startswith(('NOA_MULTI_TENANT=','NOA_GATEWAY_GID='))]
lines+=['NOA_MULTI_TENANT=1',f'NOA_GATEWAY_GID={gateway.pw_gid}']
environment.write_text('\n'.join(lines)+'\n')
PY
cat > /etc/systemd/system/noatech-tenants.service <<'UNIT'
[Unit]
Description=NOA restricted multi-account provisioning broker
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=exec
User=root
Group=noactrl
WorkingDirectory=/opt/noatech/tenant-app
RuntimeDirectory=noatech-tenants
RuntimeDirectoryMode=0750
ExecStart=/opt/noatech/venv/bin/python -m scripts.tenant_controller
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths=/etc/noatech /etc/noatech-worker-policies /etc/systemd/system /var/lib/noatech-tenants /run/noatech-tenants /tmp
MemoryMax=384M
TasksMax=96
LimitCORE=0

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/noatech-gateway.service <<'UNIT'
[Unit]
Description=NOA isolated browser-session gateway
Requires=noatech-tenants.service
After=noatech-tenants.service

[Service]
Type=exec
User=noagate
Group=noagate
WorkingDirectory=/opt/noatech/tenant-app
ExecStart=/opt/noatech/venv/bin/python -m scripts.tenant_gateway
Environment=NOA_GATEWAY_TOKEN_FILE=/etc/noatech-gateway/token
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
MemoryMax=256M
TasksMax=64
LimitCORE=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now noatech-tenants noatech-gateway
