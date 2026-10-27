from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from config import load_config
from core.data_feed import DataFeed
from core.market_hours import quote_session,open_seconds_between,bar_close_utc
from signals.filters import assess_spread


def utc(value):
    return datetime.fromisoformat(value.replace("Z","+00:00"))


def test_zero_heavy_broker_history_uses_hard_cap_instead_of_zero_ratio():
    status=assess_spread(5,[0]*99+[1],2,40)
    assert status["allowed"] and status["basis"]=="absolute_cap"
    assert status["limit_points"]==40 and status["zero_historical_samples"]==99
    assert not assess_spread(41,[0]*100,2,40)["allowed"]
    assert not assess_spread(5,[0]*100,2)["allowed"]


def test_positive_historical_and_live_baselines_still_reject_cost_spikes():
    assert not assess_spread(5,[1]*100,2,40)["allowed"]
    status=assess_spread(5,[0]*100,2,40,live_samples=[2]*20)
    assert not status["allowed"] and status["basis"]=="recent_ticks"
    assert not assess_spread(41,[100]*100,2,40,live_samples=[100]*20)["allowed"]


@pytest.mark.parametrize("spread",[None,float("nan"),float("inf"),-1])
def test_invalid_spread_cannot_pass_any_fallback(spread):
    assert not assess_spread(spread,[0]*100,2,40)["allowed"]


def test_rounding_at_absolute_point_limit_is_not_a_false_rejection():
    assert assess_spread(40.00000000001,[0]*100,2,40)["allowed"]
    assert not assess_spread(40.001,[0]*100,2,40)["allowed"]


def test_live_spread_samples_count_distinct_quotes_and_expire(broker):
    now=[utc("2026-10-27T12:00:00Z")]
    broker.quote.time=now[0].timestamp()
    feed=DataFeed(broker,clock=lambda:now[0])
    for _ in range(30):
        feed.spread_status("EURUSD",[0]*100,2,40)
    assert len(feed._spread_samples["EURUSD"])==1
    for _ in range(20):
        now[0]+=timedelta(seconds=1);broker.quote.time=now[0].timestamp()
        feed.spread_status("EURUSD",[0]*100,2,40)
    assert feed.spread_status("EURUSD",[0]*100,2,40)["basis"]=="recent_ticks"
    now[0]+=timedelta(seconds=301);broker.quote.time=now[0].timestamp()
    assert feed.spread_status("EURUSD",[0]*100,2,40)["basis"]=="absolute_cap"


def test_stale_quotes_never_train_live_spread_baseline(broker):
    now=utc("2026-10-27T12:00:00Z");broker.quote.time=(now-timedelta(minutes=1)).timestamp()
    feed=DataFeed(broker,clock=lambda:now)
    for _ in range(30):feed.spread_status("EURUSD",[0]*100,2,40)
    assert not feed._spread_samples["EURUSD"]


@pytest.mark.parametrize("date,gold_open,index_open",[
    ("2026-10-27T21:30:00Z","2026-10-27T22:00:00+00:00","2026-10-27T22:05:00+00:00"),
    ("2026-11-03T22:30:00Z","2026-11-03T23:00:00+00:00","2026-11-03T23:05:00+00:00"),
    ("2026-03-10T21:30:00Z","2026-03-10T22:00:00+00:00","2026-03-10T22:05:00+00:00")])
def test_us_market_pause_uses_us_dst_independently_of_server_dst(date,gold_open,index_open):
    symbols=load_config().symbols["symbols"]
    gold=quote_session(symbols["XAUUSD"]["quote_schedule"],utc(date))
    index=quote_session(symbols["NAS100"]["quote_schedule"],utc(date))
    assert gold["scheduled_closed"] and gold["next_open_utc"]==gold_open
    assert index["scheduled_closed"] and index["next_open_utc"]==index_open


def test_published_forex_dst_exception_and_reopening_still_require_fresh_quote(broker):
    cfg=load_config()
    now=utc("2026-10-25T21:01:00Z")
    schedule=cfg.symbols["symbols"]["EURUSD"]["quote_schedule"]
    assert not quote_session(schedule,now)["scheduled_closed"]
    feed=DataFeed(broker,15,cfg.symbols,clock=lambda:now)
    broker.quote.time=(now-timedelta(minutes=1)).timestamp()
    assert feed.tick_status("EURUSD_o")["status"]=="STALE_TICK"
    broker.quote.time=now.timestamp()
    assert feed.tick_status("EURUSD_o")["fresh"]


def test_holiday_exception_is_explicit_and_does_not_shift_timezone():
    schedule={"type":"weekly_sessions","timezone":"America/New_York",
        "weekly":{0:[["09:00","17:00"]],1:[["09:00","17:00"]]},"exceptions":{"2026-10-26":[]}}
    status=quote_session(schedule,utc("2026-10-26T14:00:00Z"))
    assert status["scheduled_closed"] and status["next_open_utc"]=="2026-10-27T13:00:00+00:00"


def test_weekend_gap_does_not_make_completed_h4_context_permanently_stale():
    now=utc("2026-10-25T22:10:00Z")
    schedule={"type":"litefinance_forex","timezone":"Europe/Helsinki"}
    assert open_seconds_between(schedule,utc("2026-10-23T21:00:00Z"),now)==299
    class Client:
        market_timezone=ZoneInfo("Europe/Helsinki")
        def timeframe(self,tf):return tf
        def copy_rates_from_pos(self,symbol,tf,start,count):
            opened=utc("2026-10-23T17:00:00Z") if tf=="H4" else utc("2026-10-23T20:55:00Z")
            return [{"time":int(opened.timestamp()),"open":1,"high":2,"low":.5,"close":1}]
    feed=DataFeed(Client(),symbol_config={"symbols":{"EURUSD":{"quote_schedule":schedule}}},clock=lambda:now)
    assert len(feed.bars("EURUSD","H4",1,allow_session_gaps=True))==1
    with pytest.raises(ValueError,match="stale"):
        feed.bars("EURUSD","M5",1) # Friday's entry setup is not revived on Monday.


@pytest.mark.parametrize("opened,hours",[("2026-03-28T22:00:00Z",23),("2026-10-24T21:00:00Z",25)])
def test_daily_bar_boundaries_follow_server_dst(opened,hours):
    start=utc(opened);closed=bar_close_utc(start,86400,ZoneInfo("Europe/Helsinki"))
    assert (closed-start).total_seconds()==hours*3600


def test_invalid_weekly_session_is_rejected_by_runtime_config():
    cfg=load_config().model_copy(deep=True)
    cfg.symbols["symbols"]["XAUUSD"]["quote_schedule"]["weekly"][0]=[["17:00","09:00"]]
    with pytest.raises(ValueError,match="end must follow"):
        cfg.validate_runtime()
