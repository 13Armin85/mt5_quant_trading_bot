from types import SimpleNamespace
from datetime import datetime, timezone
import json
import pandas as pd
import pytest

from config import load_config
from core.trading_engine import TradingEngine
from core.execution import ExecutionEngine
from core.risk_manager import RiskManager
from core.data_feed import DataFeed
from core.position_manager import PositionManager
from database.repository import Repository
from news.news_filter import NewsFilter
from utils.types import TradingMode, Direction, Signal, OrderRequest


@pytest.fixture
def paper_runtime(tmp_path, broker, risk_cfg, monkeypatch):
    cfg=load_config().model_copy(deep=True)
    cfg.settings["trading"].update(risk_cfg)
    cfg.settings["news"]["enabled"]=False
    cfg.symbols={"symbols":{"EURUSD":{"broker_symbol":"EURUSD","currencies":["EUR","USD"]}}}
    repo=Repository("sqlite:///"+str(tmp_path/"paper.db"))
    risk=RiskManager(cfg.get("trading"),broker)
    execution=ExecutionEngine(broker,TradingMode.PAPER,False,str(tmp_path/"kill.flag"),risk,repo,cfg.get("trading"),"paper-account")
    frames=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=220,freq="5min",tz="UTC"),"close":[1.1]*220,"atr":[.001]*220,"spread":[20]*220})
    feed=DataFeed(broker)
    monkeypatch.setattr(feed,"bars",lambda *a:frames.copy())
    monkeypatch.setattr("core.trading_engine.add_indicators",lambda df,*a:df)
    monkeypatch.setattr("core.trading_engine.detect_regime",lambda *a:None)
    monkeypatch.setattr("core.trading_engine.analyze_structure",lambda *a:{})
    monkeypatch.setattr("core.trading_engine.analyze_volume",lambda *a:{})
    engine=TradingEngine(cfg,broker,feed,risk,execution,repo,NewsFilter(cfg))
    monkeypatch.setattr(engine.signal_engine,"generate",lambda *a:Signal("EURUSD",Direction.BUY,.8,1.1,1.099,1.103,signal_id="closed-bar-one"))
    return engine


def test_runtime_paper_entry_exit_and_no_broker_order(paper_runtime,broker):
    engine=paper_runtime
    engine.process_symbol("EURUSD")
    assert len(engine.execution.paper_positions())==1 and not broker.sent
    trade=engine.repository.recent_trades()[0]
    assert trade.risk <= 100 and trade.status=="PAPER_OPEN"
    broker.quote.bid=1.098
    engine.manage_positions()
    assert not engine.execution.paper_positions() and not broker.sent
    assert engine.repository.recent_trades()[0].status=="PAPER_CLOSED"
    assert engine.repository.get_state("paper-account:paper_pnl") < 0


def test_paper_positions_restore_and_manage_under_kill_switch(paper_runtime,broker):
    engine=paper_runtime
    engine.process_symbol("EURUSD")
    engine.execution.kill_switch_path.touch()
    engine.process_symbol("EURUSD")
    assert len(engine.execution.paper_positions())==1
    restarted=TradingEngine(engine.cfg,broker,engine.feed,RiskManager(engine.cfg.get("trading"),broker),engine.execution,engine.repository,engine.news_filter)
    assert restarted.risk.state.day_start_equity==10000
    broker.quote.bid=1.104; broker.quote.ask=1.1042
    restarted.manage_positions()
    assert not engine.execution.paper_positions() and not broker.sent


def test_paper_partial_close_happens_once(paper_runtime,broker):
    engine=paper_runtime
    engine.position_manager=PositionManager({"partial_close_enabled":True,"partial_close_r":1,"partial_close_fraction":.5})
    engine.process_symbol("EURUSD")
    original=engine.execution.paper_positions()[0]["volume"]
    broker.quote.bid=1.1015; broker.quote.ask=1.1017
    engine.manage_positions()
    remaining=engine.execution.paper_positions()[0]["volume"]
    assert 0 < remaining < original
    cash=engine.repository.get_state("paper-account:paper_pnl")
    engine.manage_positions()
    assert engine.execution.paper_positions()[0]["volume"]==remaining
    assert engine.repository.get_state("paper-account:paper_pnl")==cash


def test_position_manager_never_widens_stop():
    manager=PositionManager({"break_even_enabled":True,"trailing_enabled":True})
    decision=manager.decide("BUY",100,105,104,1,2)
    assert decision.new_sl is None or decision.new_sl >= 104


def test_paper_settlement_is_idempotent(tmp_path):
    repo=Repository("sqlite:///"+str(tmp_path/"atomic.db"))
    repo.claim_execution("paper:one",{})
    repo.update_attempt("paper:one","PAPER_OPEN")
    repo.settle_paper("paper:one","paper",1,1.1,50)
    repo.settle_paper("paper:one","paper",1,1.1,50)
    assert repo.get_state("paper:paper_pnl")==50


def test_sigterm_stop_wakes_poll_and_persists_risk_without_orders(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.risk.roll_state(10000)
    monkeypatch.setattr(engine, "run_cycle", lambda *_: engine.stop())
    engine.run_forever()
    assert not engine.running and engine.stop_event.is_set()
    assert engine.repository.get_state(engine.execution.scope + ":risk")["day_start_equity"] == 10000
    assert not broker.sent


def test_stop_during_analysis_prevents_new_entry(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    generate = engine.signal_engine.generate
    def stop_after_analysis(*args):
        result = generate(*args)
        engine.stop()
        return result
    monkeypatch.setattr(engine.signal_engine, "generate", stop_after_analysis)
    engine.process_symbol("EURUSD")
    assert not engine.execution.paper_positions() and not broker.sent


def test_news_refreshes_while_market_quotes_are_closed(paper_runtime, broker, monkeypatch):
    calls = []
    monkeypatch.setattr(paper_runtime.news_filter, "refresh", lambda: calls.append("news"))
    monkeypatch.setattr(paper_runtime.feed, "tick_status", lambda *_: {"fresh": False, "status": "MARKET_CLOSED", "reason": "Market closed"})
    monkeypatch.setattr(paper_runtime.feed, "log_tick_status", lambda *_: None)
    paper_runtime.run_cycle(["EURUSD"])
    assert calls == ["news"] and not broker.sent


def test_news_is_checked_again_after_sizing_before_entry(paper_runtime, broker, monkeypatch):
    calls = []
    def evaluate(*args):
        calls.append(1)
        return (True, "Clear", .5) if len(calls) == 1 else (False, "New high impact release", 0)
    monkeypatch.setattr(paper_runtime.news_filter, "evaluate", evaluate)
    paper_runtime.process_symbol("EURUSD")
    assert len(calls) == 2 and not paper_runtime.execution.paper_positions() and not broker.sent
    decision = paper_runtime.repository.get_state(paper_runtime.execution.scope + ":decision")
    assert decision["direction"] == "HOLD" and "New high impact" in decision["news"]["reason"]


def test_chart_analysis_continues_while_news_http_is_waiting(paper_runtime, broker):
    from threading import Event
    import time
    from news.background import ConcurrentNewsFilter
    engine = paper_runtime
    engine.cfg.settings["news"]["enabled"] = True
    entered, release = Event(), Event()
    collector = NewsFilter(engine.cfg)
    collector.headlines = None
    class SlowCalendar:
        def events(self, *args):
            entered.set()
            assert release.wait(3)
            return []
    collector.provider = SlowCalendar()
    engine.news_filter = ConcurrentNewsFilter(engine.cfg, collector)
    engine.news_filter.start_background()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        engine.process_symbol("EURUSD")
        assert time.monotonic() - started < 1
        analysis = engine.repository.get_state("analysis:EURUSD")
        assert analysis and not analysis["news"]["allowed"]
        assert not engine.execution.paper_positions() and not broker.sent
    finally:
        release.set()
        engine.news_filter.stop_background()
