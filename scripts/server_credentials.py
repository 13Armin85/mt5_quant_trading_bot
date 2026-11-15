"""Create encrypted systemd credentials and an owner-account password hash."""
import grp
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from core.account_login import AccountLogin


def main():
    payload=json.loads(sys.stdin.readline(16385))
    request=AccountLogin.parse(payload)
    if request.account_type!='demo':
        raise SystemExit('This initial server activation is restricted to the requested demo account.')
    base=Path('/etc/noatech')
    base.mkdir(mode=0o750,exist_ok=True)
    os.chown(base,0,grp.getgrnam('noatech').gr_gid)
    os.chmod(base,0o750)
    encrypted=base/('mt5.cred.'+secrets.token_hex(16))
    subprocess.run(['systemd-creds','encrypt','--with-key=host','--name=mt5','-',str(encrypted)],
        input=json.dumps(payload).encode(),capture_output=True,check=True)
    os.chmod(encrypted,0o600)
    encrypted.replace(base/'mt5.cred')
    salt=secrets.token_bytes(32)
    record={'login':request.login,'account_type':request.account_type,'server':request.server,'salt':salt.hex(),
        'digest':hashlib.scrypt(request.password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32).hex()}
    access=base/('portal-access.'+secrets.token_hex(16))
    with access.open('x',encoding='utf-8') as handle:
        os.chmod(access,0o600)
        handle.write(json.dumps(record))
    os.chown(access,0,grp.getgrnam('noatech').gr_gid)
    os.chmod(access,0o640)
    access.replace(base/'portal-access.json')
    print(json.dumps({'encrypted_credentials':True,'owner_account_gate':True,'demo_account':request.login}))


if __name__=='__main__':
    main()
