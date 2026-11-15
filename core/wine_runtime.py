"""Linux account controller; the official Windows MT5 SDK runs inside Wine."""
import json
from contextlib import contextmanager
import os
import re
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import time

from core.account_login import AccountError
from core.account_portal import DesktopRuntime


class WineRuntime(DesktopRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.cfg.env.mt5_path:
            self.cfg.env.mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"

    def wine_command(self):
        return [os.environ.get("NOA_WINE", "/usr/bin/wine"), os.environ["NOA_WINE_PYTHON"]]

    def resolve_terminal_path(self, request):
        raw = request.terminal_path or self.cfg.env.mt5_path or r"C:\Program Files\MetaTrader 5\terminal64.exe"
        path = PureWindowsPath(raw)
        if path.drive.lower() != "c:" or path.name.lower() != "terminal64.exe":
            raise AccountError("مسیر ترمینال باید به terminal64.exe داخل درایو C محیط متاتریدر اشاره کند.")
        prefix = Path(os.environ["WINEPREFIX"]).resolve() / "drive_c"
        target = prefix.joinpath(*path.parts[1:]).resolve()
        if not target.is_relative_to(prefix.resolve()) or not target.is_file():
            raise AccountError("ترمینال MT5 روی سرور پیدا نشد.")
        return str(path)

    def native_probe(self, action, payload):
        try:
            result = subprocess.run([*self.wine_command(), "-m", "scripts.mt5_host", action],
                cwd=self.root, env=dict(os.environ, WINEDEBUG="-all", PYTHONUTF8="1"),
                input=json.dumps(payload), text=True, encoding="utf-8", errors="replace",
                capture_output=True, timeout=100)
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            raise AccountError("متاتریدر سرور پاسخ نداد؛ وضعیت محیط اجرای آن را بررسی کنید.") from None
        if not data.get("ok"):
            raise AccountError(data.get("error", "اتصال متاتریدر کامل نشد."))
        return data.get("account")

    def snapshot(self):
        if self.bridge_running():
            return super().snapshot()
        # A credential-free SDK initialize starts an unauthenticated terminal
        # and opens a modal Login dialog when password retention is disabled.
        # Inspect an existing terminal, but never launch one just for a snapshot.
        if not self.terminal_running():
            if self.account:
                raise AccountError("حساب قبلی در دسترس نیست؛ ابتدا اتصال آن را بازیابی کنید.")
            return None
        account = self.native_probe("snapshot", {"terminal_path":self.cfg.env.mt5_path})
        if account is None and self.account:
            raise AccountError("حساب قبلی در دسترس نیست؛ پیش از تعویض حساب، اتصال آن را بازیابی کنید.")
        return account

    def terminal_running(self):
        configured=str(self.cfg.env.mt5_path).replace('\\','/').casefold()
        windows=PureWindowsPath(self.cfg.env.mt5_path)
        native=str(Path(os.environ['WINEPREFIX'])/'drive_c'/Path(*windows.parts[1:])).casefold()
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                executable=(process/'cmdline').read_bytes().split(b'\x00',1)[0].decode('utf-8',errors='replace').replace('\\','/').casefold()
                if executable in {configured,native}:
                    return True
            except OSError:
                continue
        return False

    def authenticate_account(self, request, path):
        payload={"login":str(request.login),"password":request.password,
            "server":request.server,"account_type":request.account_type,"terminal_path":path,
            "confirm_real":request.account_type=="real", "_trading_authorized":False}
        with self.terminal_startup(request,path):
            account=self.native_probe('authenticate',payload)
            if self.trading_enabled() and not account.get('trade_allowed'):
                self.authorize_demo_session(request,account)
                account=self.native_probe('authenticate',dict(payload,_trading_authorized=True))
        return account

    @contextmanager
    def terminal_startup(self, request, path):
        directory=os.environ.get('NOA_MT5_STARTUP_DIR')
        if not directory or self.terminal_running():
            yield
            return
        if request.account_type!='demo' or any(c in request.password for c in '\r\n\x00'):
            raise AccountError('راه‌اندازی خودکار فقط برای حساب دموی معتبر مجاز است.')
        # The startup credential is confined to a 0700 systemd RuntimeDirectory
        # on /run. No password is placed in argv or persisted in the Wine prefix.
        fd,name=tempfile.mkstemp(prefix='owner-',suffix='.ini',dir=directory)
        startup=Path(name)
        try:
            with os.fdopen(fd,'w',encoding='utf-16',newline='') as handle:
                handle.write(f'[Common]\r\nLogin={request.login}\r\nServer={request.server}\r\n'
                    f'Password={request.password}\r\nKeepPrivate=0\r\n[Experts]\r\n'
                    'Enabled=0\r\nAllowLiveTrading=0\r\nAllowDllImport=0\r\nAccount=1\r\nProfile=1\r\n')
            windows_path=str(PureWindowsPath('Z:/'+str(startup).replace('\\','/').lstrip('/')))
            self.terminal_process=subprocess.Popen([self.wine_command()[0],path,'/portable',
                '/config:'+windows_path],cwd=self.root,env=dict(os.environ,WINEDEBUG='-all'),
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.wait_terminal_ready(request)
            yield
        finally:
            startup.unlink(missing_ok=True)

    def wait_terminal_ready(self, request):
        # Let Wine finish its first-process bootstrap before starting another
        # Windows process. Otherwise its service children can inherit the SDK
        # probe's pipes and prevent communicate() from reaching EOF.
        pattern=f'^{request.login} - {re.escape(request.server)}: Demo Account -'
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            result=subprocess.run(['xdotool','search','--onlyvisible','--name',pattern],
                capture_output=True,text=True,timeout=5)
            if result.returncode==0 and len(result.stdout.split())==1:
                return
            time.sleep(1)
        raise AccountError('پنجرهٔ حساب دموی موردنظر در مهلت راه‌اندازی آماده نشد.')

    def authorize_demo_session(self, request, account):
        # Never change terminal permissions before the SDK has authenticated
        # the password and verified this exact account's actual DEMO mode.
        if not self.trading_enabled() or request.account_type!='demo' or (
            account.get('login'),account.get('server'),account.get('account_type')) != (
                request.login,request.server,'demo'):
            raise AccountError('اجازهٔ معامله فقط برای حساب دموی تأییدشده معتبر است.')
        if not account.get('account_trade_allowed') or not account.get('expert_allowed') or account.get('python_api_disabled'):
            raise AccountError('مجوز معاملاتی حساب یا API پایتون فعال نیست؛ تنظیمات حساب را بررسی کنید.')
        pattern=f'^{request.login} - {re.escape(request.server)}: Demo Account -'
        try:
            found=subprocess.run(['xdotool','search','--onlyvisible','--name',pattern],
                capture_output=True,text=True,timeout=5)
            windows=found.stdout.split()
            if found.returncode or len(windows)!=1 or not windows[0].isdigit():
                raise ValueError('Exact demo terminal window is required')
            subprocess.run(['xdotool','windowfocus','--sync',windows[0],
                'key','--clearmodifiers','ctrl+e'],capture_output=True,timeout=5,check=True)
        except (OSError,ValueError,subprocess.SubprocessError):
            raise AccountError('فعال‌سازی نشست همین حساب دمو کامل نشد؛ ترمینال را بررسی کنید.') from None

    def trading_enabled(self):
        return Path(os.environ.get('NOA_DEMO_TRADING_FLAG','/etc/noatech/demo-trading.enabled')).is_file()

    def bridge_command(self):
        command = [*self.wine_command(), "-m", "core.mt5_bridge", "--session-stdin"]
        return [*command,'--allow-live'] if self.trading_enabled() else command
