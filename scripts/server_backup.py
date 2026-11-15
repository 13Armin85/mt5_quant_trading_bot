"""Create an owner-encrypted, consistent recovery snapshot without stopping MT5.

Run as root from the application directory. The recipient private key belongs
only on the owner's computer; the server stores its public certificate.
"""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile


def main():
    if os.geteuid() != 0:
        raise SystemExit('Root is required for the recovery snapshot')
    os.umask(0o077)
    app=Path('/opt/noatech/app')
    destination=Path('/var/backups/noatech')
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime('noa-%Y%m%dT%H%M%S%fZ.cms')
    target=destination/name
    pending=destination/(name+'.partial')
    # sqlite.backup obtains a consistent snapshot while the trader is running.
    # Its only unencrypted temporary copy resides on the private /run tmpfs.
    try:
        with tempfile.TemporaryDirectory(prefix='noa-backup-',dir='/run') as temporary:
            snapshot=Path(temporary)/'trading_bot.db'
            with sqlite3.connect(f'file:{app}/.docker/data/trading_bot.db?mode=ro',uri=True) as source:
                with sqlite3.connect(snapshot) as copy:
                    source.backup(copy)
            bridge_snapshot=Path(temporary)/'bridge_requests.db'
            with sqlite3.connect('file:/var/lib/noatech/state/bridge_requests.db?mode=ro',uri=True) as source:
                with sqlite3.connect(bridge_snapshot) as copy:
                    source.backup(copy)
            command=['openssl','cms','-encrypt','-binary','-aes-256-gcm',
                '-outform','DER','-out',str(pending),'-recip',
                '/etc/noatech/backup-recipient.pem','-keyopt','rsa_padding_mode:oaep']
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=process.stdin,mode='w|gz') as archive:
                    archive.add(snapshot,arcname='data/trading_bot.db')
                    archive.add(bridge_snapshot,arcname='state/bridge_requests.db')
                    for path,relative in [
                        (app/'config','config'),
                        (Path('/etc/noatech'),'etc/noatech'),
                        (Path('/var/lib/systemd/credential.secret'),'etc/systemd/credential.secret'),
                        (Path('/etc/systemd/system/noatech-portal.service'),'units/noatech-portal.service'),
                        (app/'compose.server.yaml','compose.server.yaml'),
                    ]:
                        archive.add(path,arcname=relative)
                    metadata=json.dumps({'format':1,'created_at':datetime.now(timezone.utc).isoformat(),
                        'database':'SQLite online backup','source':'NOA demo deployment'}).encode()
                    entry=tarfile.TarInfo('manifest.json'); entry.size=len(metadata); entry.mode=0o600
                    archive.addfile(entry,io.BytesIO(metadata))
                process.stdin.close()
                if process.wait(timeout=60):
                    raise RuntimeError('Snapshot encryption failed')
            except BaseException:
                process.kill(); process.wait()
                raise
        os.replace(pending,target)
        # Retain only complete encrypted snapshots from this tool.
        for old in sorted(destination.glob('noa-*.cms'),reverse=True)[14:]:
            old.unlink()
        print(json.dumps({'encrypted_snapshot':str(target),'bytes':target.stat().st_size}))
    finally:
        pending.unlink(missing_ok=True)


if __name__=='__main__':
    main()
