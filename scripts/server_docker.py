"""Root-owned, peer-authenticated Unix socket exposing four fixed operations.

The Wine/portal user must not have access to the Docker socket or group. Only
this controller uses Docker, with root-owned configuration and pinned mounts.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess

from core.server_control import MAX_CONTROL_BYTES

APP = Path('/opt/noatech/app')
ACCESS = Path('/etc/noatech/portal-access.json')
TRADING_FLAG = Path('/etc/noatech/demo-trading.enabled')
SOCKET = '/run/noatech-docker/control.sock'
OPERATIONS = {
    ('ps', '--status', 'running', '--services'),
    ('stop', 'trader'),
    ('start', 'trader'),
    ('up', '-d', '--no-build', 'trader'),
}


def service_gid():
    import grp
    return grp.getgrnam('noatech').gr_gid


def execute(args):
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args) or tuple(args) not in OPERATIONS:
        raise ValueError('Operation is not allowed')
    record = json.loads(ACCESS.read_text())
    if record['account_type'] != 'demo' or type(record['login']) is not int or record['login'] <= 0:
        raise ValueError('Only the authorized demo account may run')
    # The request cannot select a Compose file, mount, image, identity or env.
    trading = TRADING_FLAG.is_file()
    env = {'PATH':'/usr/bin:/bin', 'HOME':'/var/empty',
        'NOA_SERVICE_GID':str(service_gid()),
        'MT5_LOGIN':str(record['login']), 'MT5_SERVER':record['server'],
        'NOA_RUN_MODE':'live' if trading else 'paper',
        'ENABLE_LIVE_TRADING':'true' if trading else 'false'}
    result = subprocess.run(['/usr/bin/docker','compose','--env-file','/dev/null',
        '-f',str(APP/'compose.server.yaml'),*args],cwd=APP,env=env,
        capture_output=True,text=True,timeout=210)
    return {'returncode':result.returncode,'stdout':result.stdout[-18000:],
        'stderr':'Compose operation failed' if result.returncode else ''}


def main():
    import pwd
    allowed_uid = pwd.getpwnam('noatech').pw_uid
    os.umask(0o007)
    path = Path(SOCKET)
    if path.exists():
        path.unlink()  # RuntimeDirectory is root-owned; untrusted users cannot replace it.
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        os.chmod(SOCKET,0o660)
        server.listen(8)
        while True:
            connection,_ = server.accept()
            with connection:
                connection.settimeout(5)
                _,uid,_ = struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid != allowed_uid:
                    continue
                try:
                    with connection.makefile('rb') as handle:
                        raw = handle.readline(4097)
                    if len(raw)>4096 or not raw.endswith(b'\n'):
                        raise ValueError('Invalid request')
                    result = execute(json.loads(raw))
                except Exception:
                    result = {'returncode':1,'stdout':'','stderr':'Controller request failed'}
                body = json.dumps(result).encode()+b'\n'
                if len(body)>MAX_CONTROL_BYTES:
                    body = b'{"returncode":1,"stdout":"","stderr":"Response too large"}\n'
                try:
                    connection.sendall(body)
                except OSError:
                    pass


if __name__ == '__main__':
    main()
