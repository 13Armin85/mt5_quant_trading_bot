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


def consistent_snapshot(source, destination, username):
    """Never parse a tenant-controlled SQLite file with root privileges."""
    code="import sqlite3,sys; a=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); b=sqlite3.connect(sys.argv[2]); a.backup(b); b.close(); a.close()"
    # PID 1 starts the parser directly as the account UID. The root archiver
    # never loads the SQLite file or executes a tenant-supplied command.
    subprocess.run(['systemd-run','--quiet','--wait','--collect','--pipe',
        '--uid='+username,'--gid='+username,'--property=NoNewPrivileges=true',
        '--property=ProtectSystem=strict','--property=ProtectHome=true',
        '--property=PrivateTmp=true','--property=MemoryMax=128M',
        '--property=ReadWritePaths='+str(destination.parent),
        '/usr/bin/python3','-c',code,str(source),str(destination)],cwd='/',
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)


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
            import pwd
            os.chmod(temporary,0o711)
            owner=pwd.getpwnam('noatech')
            owner_dir=Path(temporary)/'owner';owner_dir.mkdir(mode=0o700)
            os.chown(owner_dir,owner.pw_uid,owner.pw_gid)
            snapshot=owner_dir/'trading_bot.db'
            consistent_snapshot(app/'.docker/data/trading_bot.db',snapshot,'noatech')
            bridge_snapshot=owner_dir/'bridge_requests.db'
            consistent_snapshot(Path('/var/lib/noatech/state/bridge_requests.db'),bridge_snapshot,'noatech')
            extra=[]
            preferences={'owner_stopped':(app/'.docker/control/trading_stopped').exists(),'tenants':{}}
            for record_path in Path('/etc/noatech/tenants').glob('*/record.json'):
                record=json.loads(record_path.read_text())
                if record.get('legacy') or not record.get('verified'):
                    continue
                key=record['key'];user=pwd.getpwnam(record['username'])
                preferences['tenants'][key]={'stopped':(Path(record['work'])/'.docker/control/trading_stopped').exists()}
                directory=Path(temporary)/key;directory.mkdir(mode=0o700)
                os.chown(directory,user.pw_uid,user.pw_gid)
                for source,name in [(Path(record['work'])/'.docker/data/trading_bot.db','trading_bot.db'),
                    (Path(record['home'])/'state/bridge_requests.db','bridge_requests.db')]:
                    if source.is_file():
                        tenant_snapshot=directory/name
                        consistent_snapshot(source,tenant_snapshot,record['username'])
                        extra.append((tenant_snapshot,'tenants/'+key+'/'+name))
            command=['openssl','cms','-encrypt','-binary','-aes-256-gcm',
                '-outform','DER','-out',str(pending),'-recip',
                '/etc/noatech/backup-recipient.pem','-keyopt','rsa_padding_mode:oaep']
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=process.stdin,mode='w|gz') as archive:
                    archive.add(snapshot,arcname='data/trading_bot.db')
                    archive.add(bridge_snapshot,arcname='state/bridge_requests.db')
                    for path,name in extra:
                        archive.add(path,arcname=name)
                    preference_data=json.dumps(preferences).encode()
                    preference_entry=tarfile.TarInfo('trading_preferences.json')
                    preference_entry.size=len(preference_data);preference_entry.mode=0o600
                    archive.addfile(preference_entry,io.BytesIO(preference_data))
                    for path,relative in [
                        (app/'config','config'),
                        (Path('/etc/noatech'),'etc/noatech'),
                        (Path('/var/lib/systemd/credential.secret'),'etc/systemd/credential.secret'),
                        (Path('/etc/systemd/system/noatech-portal.service'),'units/noatech-portal.service'),
                        (app/'compose.server.yaml','compose.server.yaml'),
                        (Path('/etc/noatech-gateway'),'etc/noatech-gateway'),
                        (Path('/etc/noatech-worker-policies'),'etc/noatech-worker-policies'),
                    ]:
                        if path.exists():
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
