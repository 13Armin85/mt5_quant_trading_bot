"""One Linux UID, X display, Wine prefix, MT5 bridge and journal per account."""
import json
import os
from pathlib import Path
import subprocess
import time

from config import load_config
from core.account_login import AccountError, AccountLogin
from core.account_portal import AccountPortal, PortalServer, handler_for
from core.runtime_lock import RuntimeLock
from core.tenancy import checked_key, control
from core.wine_runtime import WineRuntime


class TenantWineRuntime(WineRuntime):
    def compose(self, *args, account=None):
        result=control({'operation':'compose','tenant':checked_key(os.environ['NOA_TENANT_ID']), 'args':list(args)})
        if result.get('returncode'):
            raise AccountError('اجرای موتور اختصاصی حساب ناموفق بود.')
        return result.get('stdout','')


def main():
    home=Path(os.environ['HOME'])
    state=Path(os.environ['NOA_HOST_STATE_DIR'])
    with RuntimeLock(state/'portal.lock'):
        display=subprocess.Popen(['Xvfb',os.environ['DISPLAY'],'-screen','0','1280x800x24',
            '-nolisten','tcp','-auth',os.environ['XAUTHORITY']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if display.poll() is not None:
                    raise RuntimeError('Private display did not start')
                ready=subprocess.run(['xdotool','getdisplaygeometry'],capture_output=True,timeout=3)
                if ready.returncode==0:
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Private display timed out')
            prefix=Path(os.environ['WINEPREFIX'])
            if not (prefix/'system.reg').exists():
                subprocess.run(['/opt/wine-devel/bin/wineboot','-u'],stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,check=True,timeout=100)
            payload=json.loads((Path(os.environ['CREDENTIALS_DIRECTORY'])/'mt5').read_text())
            request=AccountLogin.parse(payload)
            expected=(int(os.environ['NOA_TENANT_LOGIN']),os.environ['NOA_TENANT_SERVER'],os.environ['NOA_TENANT_TYPE'])
            def guard(value):
                if (value.login,value.server,value.account_type)!=expected or value.terminal_path:
                    raise AccountError('ورود فقط به حساب اختصاصی همین محیط مجاز است.')
            guard(request)
            cfg=load_config()
            cfg.validate_runtime()
            runtime=TenantWineRuntime(cfg,root=Path(os.environ['NOA_RUNTIME_ROOT']))
            runtime.login(request)
            token=Path(os.environ['ACCOUNT_PORTAL_TOKEN_FILE']).read_text().strip()
            portal=AccountPortal(token,runtime,guard)
            with PortalServer(('127.0.0.1',int(os.environ['NOA_ACCOUNT_PORTAL_PORT'])),handler_for(portal)) as server:
                print('Dedicated account environment ready.',flush=True)
                server.serve_forever()
        finally:
            display.terminate()


if __name__=='__main__':
    try:
        main()
    except Exception:
        # Never log passwords, SDK strings, account payloads or tracebacks.
        raise SystemExit('Dedicated account startup failed; inspect private service health.') from None
