"""Restore the authorized demo session from systemd's RAM-backed credentials."""
import json
import os
from pathlib import Path
import time

from config import load_config
from dashboard.account_client import AccountPortalClient, PortalUnavailable


def main():
    flag=Path(os.environ['NOA_AUTO_LOGIN_FLAG'])
    if not flag.exists():
        print('Automatic login is disabled by the account owner.')
        return
    payload=json.loads((Path(os.environ['CREDENTIALS_DIRECTORY'])/'mt5').read_text())
    if payload.get('account_type')!='demo':
        raise SystemExit('Only the authorized demo account can be restored automatically.')
    cfg=load_config()
    client=AccountPortalClient('http://127.0.0.1:18766',cfg.env.account_portal_token_file)
    for _ in range(40):
        try:
            if client.request('/status').get('ready'):
                break
        except PortalUnavailable:
            pass
        time.sleep(1)
    result=client.request('/login',payload=payload)
    if not result.get('ok'):
        # The API emits only fixed errors, never raw SDK messages or credentials.
        raise SystemExit(result.get('error','Demo session could not be restored'))
    account=result['account']
    print(json.dumps({'connected':True,'login':account['login'],'server':account['server'],
        'account_type':account['account_type']}))


if __name__=='__main__':
    main()
