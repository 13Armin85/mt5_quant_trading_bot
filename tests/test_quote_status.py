from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from core.data_feed import DataFeed
from core.market_hours import quote_session


def date(value):
    return datetime.fromisoformat(value.replace("Z","+00:00"))


FOREX={"type":"litefinance_forex","timezone":"Europe/Helsinki"}


def feed(broker,kind="litefinance_forex"):
    return DataFeed(broker,15,{"symbols":{"EURUSD":{"broker_symbol":"EURUSD_o","quote_schedule":{"type":kind,"timezone":"Europe/Helsinki"}}}})


def test_weekend_closure_accounts_for_the_october_dst_change():
    state=quote_session(FOREX,date("2026-10-23T21:39:10Z"))
    assert state["scheduled_closed"]
    assert state["next_open_utc"]=="2026-10-25T22:05:01+00:00"
    assert state["next_open_tehran"]=="2026-10-26T01:35:01+03:30"


def test_summer_forex_reopening_uses_the_summer_offset():
    state=quote_session(FOREX,date("2026-07-11T12:00:00Z"))
    assert state["next_open_utc"]=="2026-07-12T21:05:01+00:00"


def test_monday_open_boundary_is_exact():
    assert quote_session(FOREX,date("2026-10-25T22:05:00Z"))["scheduled_closed"]
    assert not quote_session(FOREX,date("2026-10-25T22:05:01Z"))["scheduled_closed"]


def test_weekend_only_does_not_invent_gold_reopening_time():
    state=quote_session({"type":"weekend_only","timezone":"Europe/Helsinki"},date("2026-10-24T12:00:00Z"))
    assert state["scheduled_closed"] and state["next_open_utc"] is None


def test_old_friday_quote_is_reported_as_market_closed(broker):
    broker.quote.time=date("2026-10-23T20:59:58Z").timestamp()
    status=feed(broker).tick_status("EURUSD_o",date("2026-10-23T21:26:21Z"))
    assert status["status"]=="MARKET_CLOSED" and not status["fresh"]
    assert status["age_seconds"]==1583
    assert status["bid"]==broker.quote.bid


def test_weekday_old_quote_remains_a_data_warning(broker):
    broker.quote.time=date("2026-10-22T10:00:00Z").timestamp()
    status=feed(broker).tick_status("EURUSD_o",date("2026-10-22T10:01:00Z"))
    assert status["status"]=="STALE_TICK" and not status["fresh"]


def test_scheduled_reopening_still_requires_a_fresh_quote(broker):
    now=date("2026-10-25T22:06:00Z")
    broker.quote.time=date("2026-10-23T20:59:58Z").timestamp()
    data=feed(broker)
    assert data.tick_status("EURUSD_o",now)["status"]=="STALE_TICK"
    broker.quote.time=now.timestamp()
    assert data.tick_status("EURUSD_o",now)["status"]=="FRESH"


def test_future_quote_is_not_hidden_by_weekend_closure(broker):
    broker.quote.time=date("2026-10-24T15:00:00Z").timestamp()
    state=feed(broker).tick_status("EURUSD_o",date("2026-10-24T12:00:00Z"))
    assert state["status"]=="CLOCK_MISMATCH" and not state["fresh"]


def test_no_schedule_does_not_assume_weekend_closure(broker):
    now=date("2026-10-24T12:00:00Z");broker.quote.time=now.timestamp()
    state=DataFeed(broker).tick_status("CRYPTO",now)
    assert state["status"]=="FRESH" and not state["schedule_known"]


def test_new_market_sessions_follow_litefinance_dst_week():
    from config import load_config
    symbols = load_config().symbols["symbols"]
    assert quote_session(symbols["BTCUSD"].get("quote_schedule"), date("2026-10-25T12:00:00Z"))["scheduled_closed"] is False
    assert quote_session(symbols["USCRUDE"]["quote_schedule"], date("2026-10-28T21:30:00Z"))["scheduled_closed"]
    assert quote_session(symbols["XAGUSD"]["quote_schedule"], date("2026-10-28T21:30:00Z"))["scheduled_closed"]
    assert quote_session(symbols["UKBRENT"]["quote_schedule"], date("2026-10-26T23:00:00Z"))["scheduled_closed"]
    assert not quote_session(symbols["UKBRENT"]["quote_schedule"], date("2026-10-27T00:05:00Z"))["scheduled_closed"]
    assert quote_session(symbols["DAX40"]["quote_schedule"], date("2026-10-28T21:30:00Z"))["scheduled_closed"]


@pytest.mark.parametrize("timestamp,bid,ask",[(float("nan"),1,2),(1,float("nan"),2),(1,2,1)])
def test_invalid_quote_never_becomes_fresh(broker,timestamp,bid,ask):
    broker.quote=SimpleNamespace(time=timestamp,bid=bid,ask=ask)
    assert feed(broker).tick_status("EURUSD_o",date("2026-10-24T12:00:00Z"))["status"]=="INVALID_TICK"


def test_repeated_closure_is_logged_once_per_minute(broker,monkeypatch):
    import core.data_feed as module
    messages=[]
    monkeypatch.setattr(module,"logger",SimpleNamespace(info=lambda *a:messages.append(a),warning=lambda *a:messages.append(a)))
    monkeypatch.setattr(module.time,"monotonic",lambda:10.)
    broker.quote.time=date("2026-10-23T20:59:58Z").timestamp()
    data=feed(broker);status=data.tick_status("EURUSD_o",date("2026-10-24T12:00:00Z"))
    data.log_tick_status(status);data.log_tick_status(status)
    assert len(messages)==1 and messages[0][2]=="MARKET_CLOSED"
