"""Local Windows account controller for the Docker dashboard.

Only this host process starts/stops Compose. The dashboard has neither the Docker
socket nor the MT5 bridge secret. Passwords travel over local pipes and stay in
process memory; account history and execution deduplication remain persistent.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
import time

import httpx

from config import ROOT, load_config
from core.account_login import AccountError, AccountLogin, verify_account
from core.mt5_client import MT5Client, MT5Error
from core.mt5_remote import RemoteMT5Client
from core.runtime_lock import RuntimeLock


def terminal_path(configured=""):
    candidates = [Path(configured)] if configured else []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.append(Path(base) / "MetaTrader 5" / "terminal64.exe")
            for pattern in ("*MetaTrader*", "*LiteFinance*", "*LiteForex*"):
                candidates.extend(Path(base).glob(pattern + "/terminal64.exe"))
    for candidate in candidates:
        if candidate.is_file() and candidate.name.lower() == "terminal64.exe":
            return str(candidate.resolve())
    raise AccountError("ترمینال MT5 پیدا نشد. آن را نصب کنید یا مسیر terminal64.exe را در گزینه‌های اتصال وارد کنید.")


class DesktopRuntime:
    def __init__(self, cfg=None, root=ROOT):
        self.cfg = (cfg or load_config()).model_copy(deep=True)
        self.root = Path(root)
        self.bridge_process = None
        self.account = None
        self.started_at = None

    def compose(self, *args, account=None):
        env = dict(os.environ)
        env.pop("MT5_PASSWORD", None)
        if account:
            env.update(MT5_LOGIN=str(account["login"]), MT5_SERVER=account["server"], ENABLE_LIVE_TRADING="true")
        try:
            result = subprocess.run(["docker", "compose", "-f", str(self.root / "compose.yaml"), *args],
                cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=210)
        except (OSError, subprocess.TimeoutExpired):
            raise AccountError("Docker پاسخ نمی‌دهد. Docker Desktop را بررسی کنید و دوباره تلاش کنید.") from None
        if result.returncode:
            with (self.root / "logs" / "portal.runtime.log").open("a", encoding="utf-8") as handle:
                handle.write((result.stdout + result.stderr)[-20000:] + "\n")
            raise AccountError("اجرای سرویس Docker ناموفق بود. Docker Desktop را بررسی کنید؛ جزئیات در logs ثبت می‌شود.")
        return result.stdout

    def bridge_client(self):
        env = self.cfg.env.model_copy(update={"mt5_bridge_url": "http://127.0.0.1:18765",
            "mt5_login": None, "mt5_server": None, "mt5_password": None})
        client = RemoteMT5Client(env, self.cfg.get("trading.mt5_time_zone", "UTC"))
        client.http.timeout = httpx.Timeout(3)
        return client

    @staticmethod
    def bridge_running():
        try:
            with socket.create_connection(("127.0.0.1", 18765), timeout=.5):
                return True
        except OSError:
            return False

    def snapshot(self):
        """Inspect exposure before switching; unavailable accounts fail closed."""
        if self.bridge_running():
            client = self.bridge_client()
        else:
            env = self.cfg.env.model_copy(update={"mt5_login": None, "mt5_server": None,
                "mt5_password": None, "mt5_path": terminal_path(self.cfg.env.mt5_path or "")})
            client = MT5Client(env, self.cfg.get("trading.mt5_time_zone", "UTC"))
        try:
            client.initialize()
            a = client.account_info()
            positions, orders = client.positions_get(), client.orders_get()
            if positions is None or orders is None:
                raise AccountError("وضعیت معاملات حساب قبلی قابل بررسی نیست؛ اتصال جدید متوقف شد.")
            return {"login": int(a.login), "server": str(a.server), "positions": len(positions), "orders": len(orders)}
        except MT5Error:
            # A clean terminal with no account may accept its first login. If a
            # portal/bridge was already managing an account, never assume no risk.
            if self.account or self.bridge_running():
                raise AccountError("حساب قبلی در دسترس نیست؛ پیش از تعویض حساب، اتصال آن را بازیابی کنید.") from None
            return None
        finally:
            client.shutdown()

    def unresolved(self):
        path = self.root / ".docker" / "data" / "trading_bot.db"
        if not path.exists():
            return False
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_attempts'").fetchone():
                    return False
                for status, payload in db.execute("SELECT status,payload FROM execution_attempts WHERE key LIKE 'LIVE:%'"):
                    data = json.loads(payload)
                    if status in {"SUBMITTING", "UNKNOWN", "PROTECTION_FAILURE"} or any(
                        data.get(key) in {"SUBMITTING", "UNKNOWN"} for key in ("partial_state", "close_state")):
                        return True
            return False
        except (sqlite3.Error, ValueError):
            raise AccountError("ژورنال اجرا قابل بررسی نیست؛ برای حفظ تاریخچه، اتصال جدید متوقف شد.") from None

    def check_switch(self, previous, request):
        if previous and (previous["login"], previous["server"]) != (request.login, request.server):
            if previous["positions"] or previous["orders"] or self.unresolved():
                raise AccountError("حساب قبلی معاملهٔ باز، سفارش معلق یا اجرای نامشخص دارد؛ ابتدا همان حساب را بررسی کنید.")
        elif previous is None and self.unresolved():
            raise AccountError("اجرای نامشخص در ژورنال وجود دارد؛ ابتدا حساب قبلی را بازیابی کنید.")

    def stop_bridge(self):
        if self.bridge_running():
            client = self.bridge_client()
            try:
                client._rpc("stop_bridge")
            except MT5Error:
                raise AccountError("پل هنوز در اختیار موتور قبلی است؛ کمی صبر کنید و دوباره تلاش کنید.") from None
            finally:
                client.http.close()
            deadline = time.monotonic() + 12
            while self.bridge_running() and time.monotonic() < deadline:
                time.sleep(.1)
            if self.bridge_running():
                raise AccountError("پل اتصال قبلی هنوز متوقف نشده است.")
        if self.bridge_process is not None:
            try:
                self.bridge_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise AccountError("فرآیند اتصال قبلی هنوز فعال است.") from None
            self.bridge_process = None

    def launch_bridge(self, request, path):
        payload = {"broker": "LiteFinance", "login": str(request.login), "password": request.password,
            "account_type": request.account_type, "server": request.server, "terminal_path": path,
            "confirm_real": request.account_type == "real"}
        env = dict(os.environ, PYTHONUTF8="1")
        env.pop("MT5_PASSWORD", None)
        with (self.root / "logs" / "bridge.stdout.log").open("a", encoding="utf-8") as out, \
                (self.root / "logs" / "bridge.stderr.log").open("a", encoding="utf-8") as err:
            self.bridge_process = subprocess.Popen(
                [sys.executable, "-m", "core.mt5_bridge", "--allow-live", "--session-stdin"],
                cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self.bridge_process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                self.bridge_process.stdin.close()
            except OSError:
                raise AccountError("راه‌اندازی پل متاتریدر ناموفق بود.") from None
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.bridge_process.poll() is not None:
                raise AccountError("پل متاتریدر شروع نشد؛ اجرای هم‌زمان دیگری ممکن است فعال باشد.")
            if self.bridge_running():
                client = self.bridge_client()
                try:
                    client.initialize()
                    verify_account(client.account_info(), client.terminal_info(), request)
                    if not client.bridge_live:
                        raise AccountError("مجوز معامله در پل اتصال فعال نشده است.")
                    return
                finally:
                    client.shutdown()
            time.sleep(.15)
        raise AccountError("زمان انتظار برای اتصال متاتریدر تمام شد؛ دوباره تلاش کنید.")

    def login(self, request):
        path = terminal_path(request.terminal_path or self.cfg.env.mt5_path or "")
        if not self.account and not self.bridge_running():
            self.cfg.env.mt5_path = path
        self.check_switch(self.snapshot(), request)
        was_running = "trader" in self.compose("ps", "--status", "running", "--services").splitlines()
        self.compose("stop", "trader")
        try:
            # Inspect again after the engine releases its lease: an order could
            # have filled between the first inspection and graceful shutdown.
            self.check_switch(self.snapshot(), request)
        except AccountError:
            if was_running:
                self.compose("start", "trader")
            raise
        self.stop_bridge()
        env = self.cfg.env.model_copy(update={"mt5_login": request.login, "mt5_server": request.server,
            "mt5_password": request.password, "mt5_path": path})
        client = MT5Client(env, self.cfg.get("trading.mt5_time_zone", "UTC"))
        try:
            client.authenticate()
            account = verify_account(client.account_info(), client.terminal_info(), request)
        except MT5Error:
            raise AccountError("ورود ناموفق بود؛ شماره حساب، رمز معاملاتی و سرور را بررسی کنید.") from None
        finally:
            client.shutdown()
        self.account = account
        self.cfg.env.mt5_path = path
        self.started_at = time.time()
        try:
            self.launch_bridge(request, path)
            self.compose("up", "-d", "--no-build", "trader", account=account)
        except Exception:
            self.stop_bridge()
            raise
        return dict(account, started_at=self.started_at, scope=self.scope())

    def scope(self):
        if not self.account:
            return None
        identity = hashlib.sha256(f"{self.account['server']}:{self.account['login']}".encode()).hexdigest()[:16]
        return f"LIVE:{identity}:{int(self.cfg.get('trading.magic', 260912))}"

    def status(self):
        connected = False
        if self.account and self.bridge_running():
            client = self.bridge_client()
            try:
                client.initialize()
                a = client.account_info()
                connected = client.health_check() and (int(a.login), str(a.server)) == (self.account["login"], self.account["server"])
            except MT5Error:
                pass
            finally:
                client.shutdown()
        return {"connected": connected, "account": self.account, "scope": self.scope(), "started_at": self.started_at}

    def logout(self):
        self.compose("stop", "trader")
        self.stop_bridge()
        self.started_at = None


class AccountPortal:
    def __init__(self, token, runtime):
        if len(token) < 32:
            raise ValueError("Portal token must be at least 32 characters")
        self.token, self.runtime = token, runtime
        self.session = None
        self.attempts = deque()
        self.stop_requested = False

    def authorized(self, header):
        return hmac.compare_digest(header or "", "Bearer " + self.token)

    def session_valid(self, session):
        return bool(self.session and session and hmac.compare_digest(self.session, session))

    def dispatch(self, path, payload, session):
        if path == "/status":
            if not session:
                return {"ok": True, "ready": True}
            return {"ok": True, **self.runtime.status()}
        if path == "/login":
            now = time.monotonic()
            while self.attempts and self.attempts[0] < now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                raise AccountError("تعداد تلاش‌های ورود زیاد است؛ یک دقیقه صبر کنید.")
            self.attempts.append(now)
            request = AccountLogin.parse(payload)
            self.session = None
            account = self.runtime.login(request)
            self.session = secrets.token_urlsafe(32)
            return {"ok": True, "session": self.session, "account": account}
        if path in {"/logout", "/shutdown"}:
            self.runtime.logout()
            self.session = None
            self.stop_requested = path == "/shutdown"
            return {"ok": True}
        raise AccountError("درخواست پشتیبانی نمی‌شود.")


def handler_for(portal):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, data):
            body = json.dumps(data, ensure_ascii=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def handle_request(self, post=False):
            if not portal.authorized(self.headers.get("Authorization")) or self.headers.get("Origin"):
                self.respond(403, {"ok": False, "error": "Forbidden"})
                return
            routes = {"/login", "/logout", "/shutdown"} if post else {"/status"}
            if self.path not in routes:
                self.respond(404, {"ok": False, "error": "Not found"})
                return
            session = self.headers.get("X-Account-Session")
            if (self.path == "/logout" or (self.path == "/status" and session)) and not portal.session_valid(session):
                self.respond(401, {"ok": False, "expired": True})
                return
            try:
                payload = None
                if post:
                    size = int(self.headers.get("Content-Length", 0))
                    if not 0 < size <= 16384:
                        raise AccountError("درخواست ورود معتبر نیست.")
                    payload = json.loads(self.rfile.read(size))
                result = portal.dispatch(self.path, payload, session)
                self.respond(200, result)
            except AccountError as exc:
                self.respond(400, {"ok": False, "error": str(exc)})
            except (ValueError, TypeError, KeyError):
                self.respond(400, {"ok": False, "error": "اطلاعات درخواست معتبر نیست."})
            except Exception:
                # No traceback, exception text, payload, password or SDK error.
                self.respond(503, {"ok": False, "error": "اتصال کامل نشد؛ وضعیت Docker و MT5 را بررسی و دوباره تلاش کنید."})
            if portal.stop_requested:
                Thread(target=self.server.shutdown, daemon=True).start()

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request(post=True)
    return Handler


class PortalServer(HTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(15)
        return sock, address


def main():
    parser = argparse.ArgumentParser(description="Local MT5 login controller")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    token = Path(cfg.env.account_portal_token_file).read_text(encoding="utf-8").strip()
    if args.probe or args.stop:
        from dashboard.account_client import AccountPortalClient, PortalUnavailable
        client = AccountPortalClient("http://127.0.0.1:18766", cfg.env.account_portal_token_file)
        try:
            result = client.request("/shutdown", payload={}) if args.stop else client.request("/status")
            raise SystemExit(0 if result.get("ok") else 1)
        except PortalUnavailable:
            raise SystemExit(1) from None
    cfg.validate_runtime()
    with RuntimeLock(ROOT / ".docker" / "portal.lock"):
        portal = AccountPortal(token, DesktopRuntime(cfg))
        with PortalServer(("127.0.0.1", 18766), handler_for(portal)) as server:
            print("Account portal ready on 127.0.0.1:18766; waiting for browser login.", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
