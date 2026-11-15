from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.account_login import SERVERS
from dashboard.account_client import AccountPortalClient


@pytest.fixture
def login_app(monkeypatch):
    calls = []
    def request(self, path, *, payload=None, session=None):
        if path == "/status" and not session:
            return {"ok": True, "ready": True}
        if path == "/login":
            calls.append({k:v for k,v in payload.items() if k != "password"})
            return {"ok": False, "error": "اطلاعات ورود نادرست است."}
        return {"ok": True, "connected": True, "account": {"login":12345,"server":SERVERS["demo"],"account_type":"demo"}}
    monkeypatch.setattr(AccountPortalClient, "request", request)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard" / "app.py")).run(timeout=20)
    return app, calls


def test_first_screen_has_default_broker_demo_and_no_runtime_data(login_app):
    app, _ = login_app
    assert not app.exception
    assert app.selectbox(key="login_broker").value == "LiteFinance"
    assert app.radio(key="login_account_type").value == "demo"
    assert app.text_input(key="login_server_demo").value == SERVERS["demo"]
    assert app.text_input(key="login_password").value == ""
    assert not app.metric


def test_real_choice_updates_server_and_shows_confirmation(login_app):
    app, _ = login_app
    app.radio(key="login_account_type").set_value("real").run()
    assert not app.exception
    assert app.text_input(key="login_server_real").value == SERVERS["real"]
    assert not app.checkbox(key="login_confirm_real").value
    assert app.warning


def test_submit_reaches_backend_and_reports_login_error(login_app):
    app, calls = login_app
    app.text_input(key="login_number").set_value("12345")
    app.text_input(key="login_password").set_value("test-only-password")
    app.button[0].click().run()
    assert not app.exception and len(calls) == 1
    assert calls[0]["account_type"] == "demo" and calls[0]["server"] == SERVERS["demo"]
    assert app.error[0].value == "اطلاعات ورود نادرست است."


def test_successful_login_opens_authenticated_dashboard(monkeypatch):
    import monitoring.runtime_view
    state = {"authenticated": False}
    def request(self, path, *, payload=None, session=None):
        if path == "/login":
            state["authenticated"] = True
            return {"ok": True, "session": "test-session"}
        if not session:
            return {"ok": True, "ready": True}
        return {"ok": True, "connected": True, "account": {"login":12345,"server":SERVERS["demo"],"account_type":"demo"}}
    monkeypatch.setattr(AccountPortalClient, "request", request)
    monkeypatch.setattr(monitoring.runtime_view, "read_runtime", lambda *args, **kwargs: {
        "heartbeat": {"time": "2026-01-01T00:00:00+00:00", "mode": "LIVE", "connected": True},
        "analyses": [], "positions": [], "trades": []})
    app = AppTest.from_file(str(Path(__file__).parents[1] / "dashboard" / "app.py")).run(timeout=20)
    app.text_input(key="login_number").set_value("12345")
    app.text_input(key="login_password").set_value("test-only-password")
    app.button[0].click().run()
    assert not app.exception and state["authenticated"]
    assert app.metric and not app.text_input
