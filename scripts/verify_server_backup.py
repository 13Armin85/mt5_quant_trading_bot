"""Decrypt only in memory and verify a NOA recovery snapshot.

The owner must retain the recipient certificate and private key separately
from the server. This tool never restores files or enables trading.
"""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--key',type=Path,required=True)
    parser.add_argument('--certificate',type=Path,required=True)
    parser.add_argument('--openssl',default=shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe')
    args=parser.parse_args()
    result=subprocess.run([args.openssl,'cms','-decrypt','-binary','-inform','DER',
        '-in',str(args.snapshot.resolve()),'-inkey',str(args.key.resolve()),
        '-recip',str(args.certificate.resolve())],capture_output=True,check=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout),mode='r:gz') as archive:
        required={'data/trading_bot.db','state/bridge_requests.db','manifest.json',
            'etc/noatech/mt5.cred','etc/noatech/portal-access.json','etc/systemd/credential.secret'}
        if not required<=set(archive.getnames()):
            raise ValueError('Required recovery files are missing')
        for name in ['data/trading_bot.db','state/bridge_requests.db']:
            with sqlite3.connect(':memory:') as db:
                db.deserialize(archive.extractfile(name).read())
                if db.execute('pragma integrity_check').fetchone()!=('ok',):
                    raise ValueError('Snapshot database integrity check failed')
    print('Recovery decryption verified in memory; both SQLite databases passed integrity checks.')
    print('Encrypted snapshot SHA256: '+hashlib.sha256(args.snapshot.read_bytes()).hexdigest())


if __name__=='__main__':
    main()
