"""Linux account controller; the official Windows MT5 SDK runs inside Wine."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

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
        account = self.native_probe("snapshot", {"terminal_path":self.cfg.env.mt5_path})
        if account is None and self.account:
            raise AccountError("حساب قبلی در دسترس نیست؛ پیش از تعویض حساب، اتصال آن را بازیابی کنید.")
        return account

    def authenticate_account(self, request, path):
        return self.native_probe("authenticate", {"login":str(request.login),"password":request.password,
            "server":request.server,"account_type":request.account_type,"terminal_path":path,
            "confirm_real":request.account_type=="real"})

    def bridge_command(self):
        return [*self.wine_command(), "-m", "core.mt5_bridge", "--allow-live", "--session-stdin"]
