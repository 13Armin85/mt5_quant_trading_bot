from datetime import datetime,timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.workspace import age_seconds, number, quote_rows, stamp


def test_display_handles_missing_nonfinite_and_future_timestamps():
    assert number(None) is None and number(float("inf")) is None
    assert number("0") == 0
    assert stamp("2026-11-15T10:00:00+00:00") == "۱۳:۳۰:۰۰"
    assert stamp("invalid") == "—"
    assert age_seconds({},datetime.now(timezone.utc)) is None
    assert age_seconds({"time":"2026-11-15T10:00:01+00:00"},datetime(2026,11,15,10,tzinfo=timezone.utc)) == -1


def test_market_filters_do_not_substitute_missing_prices():
    quotes={"a":{"symbol":"A","status":"FRESH","bid":10,"ask":11},
        "b":{"symbol":"B","status":"MARKET_CLOSED"},
        "c":{"symbol":"C","status":"CLOCK_MISMATCH"}}
    assert [row["نماد"] for row in quote_rows(quotes,"قیمت تازه")] == ["A"]
    assert [row["نماد"] for row in quote_rows(quotes,"نیازمند بررسی")] == ["C"]
    assert quote_rows(quotes,"بازار بسته")[0]["فروش · Bid"] is None


def test_full_workspace_renders_analyses_news_positions_and_history(monkeypatch):
    import dashboard.login_view
    import monitoring.runtime_view
    from dashboard.account_client import AccountPortalClient
    status={"ok":True,"connected":True,"account":{"login":12345,"account_type":"demo","server":"LiteFinance-MT5-Demo","currency":"USD"}}
    monkeypatch.setattr(dashboard.login_view,"authenticate",lambda cfg:status)
    monkeypatch.setattr(AccountPortalClient,"request",lambda *args,**kwargs:status)
    now=datetime.now(timezone.utc).isoformat()
    view={"heartbeat":{"time":now,"connected":True,"mode":"LIVE","equity":10000,"planned_open_risk":25,
        "quotes":{"BTCUSD":{"symbol":"BTCUSD","status":"FRESH","bid":60000,"ask":60001}},
        "news":{"calendar_ok":True,"upcoming_events":[{"time":now,"currency":"USD","impact":"HIGH","title":"Test event"}]}},
        "analyses":[{"symbol":"BTCUSD","updated_at":now,"selection":{"assessments":[{"strategy":"ema_cross","direction":"BUY","score":.8,"eligible":True}]},
            "live_chart":{"bid":"60000","ask":"60001"},"trade_plan":{"entry":60000,"sl":59000,"tp":62000,"net_rr":2},
            "indicators":{"entry":{"trend":{"adx":"24.2"}}}}],
        "positions":[{"symbol":"BTCUSD","direction":"BUY","status":"OPEN","ticket":1}],
        "trades":[{"timestamp":now,"symbol":"BTCUSD","direction":"BUY","lot":.01,"status":"CLOSED","confidence":.8,"market_regime":"TRENDING_BULLISH"}]}
    monkeypatch.setattr(monitoring.runtime_view,"read_runtime",lambda *args,**kwargs:view)
    app=AppTest.from_file(str(Path(__file__).parents[1]/"dashboard"/"app.py")).run(timeout=30)
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["نمای کلی","تحلیل نمادها","اخبار بازار","معاملات","تنظیمات"]
    assert app.selectbox(key="noatech_analysis").value.startswith("BTCUSD")
    assert len(app.dataframe) >= 5
