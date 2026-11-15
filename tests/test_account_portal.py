import json
import sqlite3
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest

from config import load_config
from core.account_login import AccountError, AccountLogin, SERVERS, verify_account
from core.account_portal import AccountPortal, DesktopRuntime, PortalServer, handler_for
from core.mt5_client import MT5Error
from dashboard.account_client import AccountPortalClient


def payload(**overrides):
    return dict(broker="LiteFinance", login="12345", password="test-password-never-persist",
                account_type="demo", **overrides)


def account(**overrides):
    values = dict(login=12345, server=SERVERS["demo"], trade_mode=0,
                  trade_allowed=True, trade_expert=True, currency="USD")
    values.update(overrides)
    return SimpleNamespace(**values)


def terminal(**overrides):
    values = dict(connected=True, trade_allowed=True, tradeapi_disabled=False)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_login_defaults_normalizes_persian_digits_and_hides_password():
    data = payload()
    data["login"] = "۱۲۳۴۵"
    request = AccountLogin.parse(data)
    assert request.login == 12345 and request.server == SERVERS["demo"]
    assert data["password"] not in repr(request)


@pytest.mark.parametrize("change", [
    {"login": "user@example.com"}, {"login": "0"}, {"login": "1; echo unsafe"},
    {"password": ""}, {"password": None}, {"account_type": "paper"},
    {"broker": "Other"}, {"server": "other-host"}, {"terminal_path": "bad\npath"},
    {"account_type": "real", "confirm_real": False},
])
def test_invalid_requests_rejected_before_runtime(change):
    data = payload()
    data.update(change)
    with pytest.raises(AccountError):
        AccountLogin.parse(data)


def test_real_login_requires_explicit_intent_and_verified_actual_account_type():
    data = payload()
    data.update(account_type="real", confirm_real=True)
    request = AccountLogin.parse(data)
    assert request.server == SERVERS["real"]
    with pytest.raises(AccountError, match="نوع"):
        verify_account(account(server=SERVERS["real"]), terminal(), request)
    result = verify_account(account(server=SERVERS["real"], trade_mode=2), terminal(), request)
    assert result["account_type"] == "real" and "password" not in result


def test_monitoring_login_accepts_disabled_trading_but_still_checks_identity():
    request=AccountLogin.parse(payload())
    a=account(trade_allowed=False,trade_expert=False)
    t=terminal(trade_allowed=False,tradeapi_disabled=True)
    result=verify_account(a,t,request,require_trading=False)
    assert result['login']==12345 and not result['trade_allowed']
    with pytest.raises(AccountError):
        verify_account(account(login=999),t,request,require_trading=False)


@pytest.mark.parametrize("changes,target", [
    ({"login": 999}, "account"), ({"server": SERVERS["real"]}, "account"),
    ({"trade_allowed": False}, "account"), ({"trade_expert": False}, "account"),
    ({"trade_allowed": False}, "terminal"), ({"tradeapi_disabled": True}, "terminal"),
])
def test_account_identity_and_permissions_are_enforced(changes, target):
    a, t = account(), terminal()
    for key, value in changes.items():
        setattr(a if target == "account" else t, key, value)
    with pytest.raises(AccountError):
        verify_account(a, t, AccountLogin.parse(payload()))


class FakeRuntime:
    def __init__(self):
        self.logins, self.stops = [], 0

    def login(self, request):
        self.logins.append((request.login, request.account_type))
        return verify_account(account(), terminal(), request)

    def status(self):
        return {"connected": True, "account": {"login": 12345, "account_type": "demo"}}

    def logout(self):
        self.stops += 1


@pytest.fixture
def portal_http(tmp_path):
    runtime = FakeRuntime()
    portal = AccountPortal("portal-token-" * 5, runtime)
    server = PortalServer(("127.0.0.1", 0), handler_for(portal))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token_file = tmp_path / "portal.token"
    token_file.write_text(portal.token)
    client = AccountPortalClient(f"http://127.0.0.1:{server.server_port}", str(token_file))
    yield portal, runtime, server, client
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_http_login_status_logout_and_session_revocation(portal_http):
    portal, runtime, _, client = portal_http
    assert client.request("/status") == {"ok": True, "ready": True}
    first = client.request("/login", payload=payload())
    assert first["ok"] and payload()["password"] not in json.dumps(first)
    assert client.request("/status", session=first["session"])["connected"]
    second = client.request("/login", payload=payload())
    assert second["session"] != first["session"]
    assert client.request("/status", session=first["session"])["expired"]
    assert client.request("/logout", payload={}, session="invalid")["expired"]
    assert runtime.stops == 0
    assert client.request("/logout", payload={}, session=second["session"])["ok"]
    assert runtime.stops == 1 and portal.session is None


def test_http_rejects_unauthorized_origin_and_oversize_body(portal_http):
    portal, runtime, server, _ = portal_http
    url = f"http://127.0.0.1:{server.server_port}"
    headers = {"Authorization": "Bearer " + portal.token}
    with httpx.Client(trust_env=False) as http:
        assert http.get(url + "/status").status_code == 403
        assert http.get(url + "/status", headers=dict(headers, Origin="https://evil.example")).status_code == 403
        assert http.get(url + "/login", headers=headers).status_code == 404
        assert http.post(url + "/login", headers=headers, content=b"x" * 17000).status_code == 400
    assert not runtime.logins


def test_http_never_returns_raw_exception_containing_password(portal_http):
    _, runtime, _, client = portal_http
    def fail(request):
        raise RuntimeError(request.password)
    runtime.login = fail
    response = client.request("/login", payload=payload())
    assert not response["ok"] and payload()["password"] not in json.dumps(response)


def test_login_attempts_are_rate_limited(portal_http):
    _, runtime, _, client = portal_http
    for _ in range(5):
        assert client.request("/login", payload=payload())["ok"]
    assert not client.request("/login", payload=payload())["ok"]
    assert len(runtime.logins) == 5


def test_expired_session_cannot_read_account_or_stop_runtime(portal_http, monkeypatch):
    portal, runtime, _, client = portal_http
    session = client.request('/login', payload=payload())['session']
    monkeypatch.setattr('core.account_portal.time.monotonic',
                        lambda: portal.session_created_at + portal.SESSION_SECONDS + 1)
    assert client.request('/status', session=session)['expired']
    assert client.request('/logout', payload={}, session=session)['expired']
    assert runtime.stops == 0


def test_non_ascii_authentication_is_rejected_without_crashing(portal_http):
    portal, _, _, _ = portal_http
    assert not portal.authorized('Bearer رمز')
    portal.session = 'session'
    import time
    portal.session_created_at = time.monotonic()
    assert not portal.session_valid('نشست')


def test_switching_accounts_blocks_open_exposure_and_uncertain_journal(tmp_path):
    runtime = DesktopRuntime(root=tmp_path)
    request = AccountLogin.parse(payload())
    previous = dict(login=999, server=SERVERS["demo"], positions=1, orders=0)
    with pytest.raises(AccountError):
        runtime.check_switch(previous, request)
    previous.update(positions=0, orders=1)
    with pytest.raises(AccountError):
        runtime.check_switch(previous, request)
    previous["orders"] = 0
    runtime.check_switch(previous, request)
    db_path = tmp_path / ".docker" / "data" / "trading_bot.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE execution_attempts (key TEXT,status TEXT,payload TEXT)")
        db.execute("INSERT INTO execution_attempts VALUES ('LIVE:old:123:signal','UNKNOWN','{}')")
    with pytest.raises(AccountError):
        runtime.check_switch(previous, request)
    # Reconnecting to the original account remains available for reconciliation.
    previous["login"] = request.login
    runtime.check_switch(previous, request)


def test_runtime_authenticates_then_passes_secrets_only_to_native_process(monkeypatch, tmp_path):
    import core.account_portal as module
    runtime = DesktopRuntime(root=tmp_path)
    events = []
    monkeypatch.setattr(module, "terminal_path", lambda _: "terminal64.exe")
    monkeypatch.setattr(runtime, "bridge_running", lambda: False)
    monkeypatch.setattr(runtime, "snapshot", lambda: None)
    monkeypatch.setattr(runtime, "unresolved", lambda: False)
    monkeypatch.setattr(runtime, "compose", lambda *args, **kwargs: events.append((args, kwargs)) or "")
    monkeypatch.setattr(runtime, "stop_bridge", lambda: events.append("stop_bridge"))
    monkeypatch.setattr(runtime, "launch_bridge", lambda request, path: events.append("launch_bridge"))
    class NativeClient:
        def __init__(self, env, zone):
            assert env.mt5_password == payload()["password"]
        def authenticate(self): events.append("authenticated")
        def account_info(self): return account()
        def terminal_info(self): return terminal()
        def shutdown(self): pass
    monkeypatch.setattr(module, "MT5Client", NativeClient)
    result = runtime.login(AccountLogin.parse(payload()))
    assert events.index("authenticated") < events.index("launch_bridge")
    assert result["account_type"] == "demo" and result["scope"].startswith("LIVE:")
    assert payload()["password"] not in repr(events) + repr(result)
    assert not list(tmp_path.iterdir())  # No credentials or environment files written.


def test_exposure_is_rechecked_after_stopping_engine(monkeypatch, tmp_path):
    import core.account_portal as module
    runtime = DesktopRuntime(root=tmp_path)
    monkeypatch.setattr(module, "terminal_path", lambda _: "terminal64.exe")
    monkeypatch.setattr(runtime, "bridge_running", lambda: False)
    snapshots = iter([dict(login=999, server=SERVERS["demo"], positions=n, orders=0) for n in (0, 1)])
    monkeypatch.setattr(runtime, "snapshot", lambda: next(snapshots))
    monkeypatch.setattr(runtime, "unresolved", lambda: False)
    events = []
    def compose(*args, **kwargs):
        events.append(args)
        return "trader\n" if args[0] == "ps" else ""
    monkeypatch.setattr(runtime, "compose", compose)
    with pytest.raises(AccountError):
        runtime.login(AccountLogin.parse(payload()))
    assert ("stop", "trader") in events and ("start", "trader") in events
    assert not any(e[0] == "up" for e in events)


def test_authentication_checks_password_even_when_initialize_reuses_cached_account(monkeypatch):
    import core.mt5_client as module
    client = module.MT5Client(load_config().env.model_copy(update={"mt5_login":12345,
        "mt5_password":"deliberately-wrong", "mt5_server":SERVERS["demo"]}))
    checked = []
    monkeypatch.setattr(client, "initialize", lambda: True)
    monkeypatch.setattr(client, "shutdown", lambda: checked.append("shutdown"))
    fake_sdk = SimpleNamespace(login=lambda *args, **kwargs: checked.append("password-checked") or False)
    monkeypatch.setattr(module, "mt5", fake_sdk)
    with pytest.raises(MT5Error, match="authentication"):
        client.authenticate()
    assert checked == ["password-checked", "shutdown"]
