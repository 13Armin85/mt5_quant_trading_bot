"""One-shot native MT5 operation. Credentials enter on stdin, never arguments."""
import argparse
import json
import sys

from config import load_config
from core.account_login import AccountError, AccountLogin, verify_account
from core.mt5_client import MT5Client, MT5Error


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["snapshot","authenticate"])
    action=parser.parse_args().action
    payload=json.loads(sys.stdin.readline(16385))
    cfg=load_config()
    values={"mt5_login":None,"mt5_server":None,"mt5_password":None,"mt5_path":payload.get("terminal_path")}
    request=None
    if action=="authenticate":
        request=AccountLogin.parse(payload)
        values.update(mt5_login=request.login,mt5_server=request.server,mt5_password=request.password)
    client=MT5Client(cfg.env.model_copy(update=values),cfg.get("trading.mt5_time_zone","UTC"))
    try:
        if request:
            client.authenticate(timeout=15000)
            result=verify_account(client.account_info(),client.terminal_info(),request)
        else:
            try:
                client.initialize(timeout=15000)
            except MT5Error:
                print(json.dumps({"ok":True,"account":None}),flush=True)
                return
            account=client.account_info()
            positions,orders=client.positions_get(),client.orders_get()
            if positions is None or orders is None:
                raise AccountError("وضعیت معاملات متاتریدر قابل بررسی نیست.")
            result={"login":int(account.login),"server":str(account.server),"positions":len(positions),"orders":len(orders)}
        print(json.dumps({"ok":True,"account":result}),flush=True)
    except AccountError as error:
        print(json.dumps({"ok":False,"error":str(error)}),flush=True)
    except MT5Error:
        print(json.dumps({"ok":False,"error":"ورود متاتریدر ناموفق بود؛ شماره حساب، رمز و سرور را بررسی کنید."}),flush=True)
    finally:
        client.shutdown()


if __name__=="__main__":
    main()
