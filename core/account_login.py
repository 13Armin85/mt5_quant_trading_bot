"""Validated account requests shared by the local portal and its dashboard."""
from dataclasses import dataclass, field
import re


SERVERS = {"demo": "LiteFinance-MT5-Demo", "real": "LiteFinance-MT5-Live"}


class AccountError(ValueError):
    """A fixed, user-facing message; never include credentials or SDK errors."""


@dataclass(frozen=True)
class AccountLogin:
    login: int
    password: str = field(repr=False)
    account_type: str
    server: str
    terminal_path: str = ""

    @classmethod
    def parse(cls, payload):
        if not isinstance(payload, dict):
            raise AccountError("اطلاعات ورود معتبر نیست.")
        if payload.get("broker", "LiteFinance") != "LiteFinance":
            raise AccountError("در این نسخه بروکر LiteFinance پشتیبانی می‌شود.")
        kind = payload.get("account_type", "demo")
        if kind not in SERVERS:
            raise AccountError("نوع حساب را بین دمو و واقعی انتخاب کنید.")
        digits = str(payload.get("login", "")).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        if not re.fullmatch(r"[0-9]{1,18}", digits) or int(digits) <= 0:
            raise AccountError("شماره حساب معاملاتی MT5 را وارد کنید؛ ایمیل پروفایل قابل استفاده نیست.")
        password = payload.get("password")
        if not isinstance(password, str) or not password or len(password) > 256 or "\x00" in password:
            raise AccountError("رمز معاملاتی حساب را وارد کنید.")
        server = payload.get("server") or SERVERS[kind]
        if not isinstance(server, str) or not re.fullmatch(r"LiteFinance[A-Za-z0-9_. -]{1,80}", server):
            raise AccountError("نام دقیق سرور LiteFinance را از مشخصات حساب وارد کنید.")
        path = payload.get("terminal_path", "")
        if not isinstance(path, str) or len(path) > 1024 or any(c in path for c in ("\x00", "\n", "\r")):
            raise AccountError("مسیر ترمینال معتبر نیست.")
        if kind == "real" and payload.get("confirm_real") is not True:
            raise AccountError("برای حساب واقعی، اجازهٔ معامله با پول واقعی را تأیید کنید.")
        return cls(int(digits), password, kind, server.strip(), path.strip())


def verify_account(account, terminal, request):
    if account is None or terminal is None or not terminal.connected:
        raise AccountError("اتصال به سرور برقرار نشد. اینترنت و نام سرور را بررسی کنید.")
    if int(account.login) != request.login or str(account.server) != request.server:
        raise AccountError("حساب متصل با اطلاعات واردشده تطابق ندارد.")
    expected = 0 if request.account_type == "demo" else 2
    if int(account.trade_mode) != expected:
        raise AccountError("نوع واقعی حساب با انتخاب دمو/واقعی تطابق ندارد؛ اتصال متوقف شد.")
    if not account.trade_allowed or not account.trade_expert:
        raise AccountError("این حساب اجازهٔ معامله ندارد. از رمز معاملاتی به‌جای رمز سرمایه‌گذار استفاده کنید.")
    if not terminal.trade_allowed or terminal.tradeapi_disabled:
        raise AccountError("در MT5، Algo Trading را روشن و گزینهٔ Disable automated trading via external Python API را غیرفعال کنید.")
    return {"login": int(account.login), "server": str(account.server), "broker": "LiteFinance",
            "account_type": request.account_type, "currency": str(account.currency),
            "trade_allowed": True}
