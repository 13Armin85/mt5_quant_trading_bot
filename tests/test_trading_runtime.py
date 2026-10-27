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


def test_paper_defensive_exit_is_recorded_and_blocks_immediate_reentry(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.process_symbol("EURUSD")
    # Still above broker SL (1.099), but beyond 0.75 of original stop distance.
    broker.quote.bid, broker.quote.ask = 1.0992, 1.0994
    engine.execution.kill_switch_path.touch()
    load_frames = engine.load_frames
    monkeypatch.setattr(engine, "load_frames", lambda *_: pytest.fail("Loss cap must not need chart data"))
    engine.manage_positions()
    assert not engine.execution.paper_positions() and not broker.sent
    trade = engine.repository.recent_trades()[0]
    assert trade.status == "PAPER_CLOSED" and "adverse" in trade.reason
    assert engine.risk.state.consecutive_losses == 1
    engine.manage_positions()
    assert engine.risk.state.consecutive_losses == 1
    monkeypatch.setattr(engine, "load_frames", load_frames)
    engine.execution.kill_switch_path.unlink()
    monkeypatch.setattr(engine.signal_engine, "generate", lambda *a: Signal("EURUSD", Direction.BUY, .8, 1.1, 1.099, 1.103, signal_id="next-candle"))
    restarted = TradingEngine(engine.cfg, broker, engine.feed, engine.risk, engine.execution, engine.repository, engine.news_filter)
    monkeypatch.setattr(restarted.signal_engine, "generate", engine.signal_engine.generate)
    restarted.process_symbol("EURUSD")
    assert not engine.execution.paper_positions()
    assert "Cooldown" in engine.repository.get_state("paper-account:decision")["reason"]


def test_unavailable_chart_data_keeps_existing_stop_management(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.process_symbol("EURUSD")
    broker.quote.bid, broker.quote.ask = 1.1016, 1.1018
    def unavailable(*_):
        raise ValueError("stale or unclosed bar")
    monkeypatch.setattr(engine, "load_frames", unavailable)
    engine.manage_positions()
    position = engine.execution.paper_positions()[0]
    assert position["sl"] == position["price"] and not broker.sent


def test_stale_quote_does_not_trigger_defensive_exit(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.process_symbol("EURUSD")
    broker.quote.bid = 1.0992
    monkeypatch.setattr(engine.feed, "is_tick_fresh", lambda *_: False)
    engine.manage_positions()
    assert engine.execution.paper_positions() and not broker.sent


def test_live_defensive_exit_only_manages_journaled_position(paper_runtime, broker):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    owned = broker.positions[0]
    manual = SimpleNamespace(**vars(owned))
    manual.ticket, manual.magic = 999, 42
    broker.positions.append(manual)
    broker.quote.bid = 1.0992
    engine.execution.kill_switch_path.touch()
    engine.manage_positions()
    closes = [r for r in broker.sent if "position" in r]
    assert len(closes) == 1 and closes[0]["position"] == owned.ticket
    payload = json.loads(engine.repository.attempts(engine.execution.scope)[0].payload)
    assert payload["close_state"] == "DONE" and "adverse" in payload["close_reason"]


@pytest.mark.parametrize("response", [None, 10012, "timeout"])
def test_ambiguous_live_close_is_not_resent_after_restart(paper_runtime, broker, monkeypatch, response):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    broker.quote.bid = 1.0992
    calls = []
    def close(req):
        calls.append(req)
        if response == "timeout":
            raise TimeoutError("unknown broker outcome")
        return None if response is None else SimpleNamespace(retcode=response)
    monkeypatch.setattr(broker, "order_send", close)
    engine.manage_positions()
    engine.execution.reconcile()
    restarted = TradingEngine(engine.cfg, broker, engine.feed, engine.risk, engine.execution, engine.repository, engine.news_filter)
    restarted.manage_positions()
    assert len(calls) == 1
    attempt = engine.repository.attempts(engine.execution.scope)[0]
    assert json.loads(attempt.payload)["close_state"] == "UNKNOWN"
    assert not engine.execution.execute(OrderRequest("EURUSD", Direction.BUY, .1, 1.1, 1.099, 1.103, 20, signal_id="next")).success


def test_rejected_live_close_retries_from_persisted_intent(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    broker.quote.bid = 1.0992
    calls = []
    def reject(req):
        calls.append(req)
        return SimpleNamespace(retcode=10021)
    monkeypatch.setattr(broker, "order_send", reject)
    engine.manage_positions()
    # Price recovers; the already confirmed exit intent still survives.
    broker.quote.bid = 1.0997
    engine.manage_positions()
    assert len(calls) == 2


def test_analysis_invalidation_closes_paper_position(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.process_symbol("EURUSD")
    position = engine.execution.paper_positions()[0]
    opened = datetime.fromisoformat(position["created"])
    entry = pd.DataFrame({"time": pd.date_range(opened + pd.Timedelta(minutes=1), periods=2, freq="5min"),
        "close": [1.0998] * 2, "ema_20": [1.1] * 2, "rsi": [40] * 2, "macd_hist": [-.001] * 2, "atr": [.001] * 2})
    frames = {"entry": entry, "trend": pd.DataFrame([{"ema_20": 1.1, "ema_50": 1.11, "ema_200": 1.12}]),
              "setup": pd.DataFrame([{"close": 1.1}])}
    monkeypatch.setattr(engine, "load_frames", lambda *_: frames)
    engine.manage_positions()
    assert not engine.execution.paper_positions() and not broker.sent
    assert "Analysis invalidated" in engine.repository.recent_trades()[0].reason


def test_live_partial_defensive_close_uses_remaining_volume(paper_runtime, broker, monkeypatch):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    broker.quote.bid = 1.0992
    original = broker.positions[0].volume
    calls = []
    def partial_then_close(req):
        calls.append(req)
        if len(calls) == 1:
            broker.positions[0].volume /= 2
            return SimpleNamespace(retcode=10010)
        broker.positions = []
        return SimpleNamespace(retcode=10009)
    monkeypatch.setattr(broker, "order_send", partial_then_close)
    engine.manage_positions()
    engine.execution.reconcile()
    engine.manage_positions()
    assert [req["volume"] for req in calls] == [original, original / 2]
    assert not broker.positions


def test_live_managed_exit_reconciles_profit_once(paper_runtime, broker):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    position = broker.positions[0]
    broker.deals = [SimpleNamespace(magic=position.magic, comment=position.comment, position_id=position.ticket,
        entry=0, profit=0, commission=-1, swap=0, price=position.price_open)]
    broker.quote.bid = 1.0992
    engine.manage_positions()
    broker.deals.append(SimpleNamespace(magic=position.magic, comment="quant-managed-exit", position_id=position.ticket,
        entry=1, profit=-5, commission=-1, swap=0, price=1.0992))
    engine.execution.reconcile()
    engine.execution.reconcile()
    trade = engine.repository.recent_trades()[0]
    assert trade.status == "CLOSED" and trade.profit == -7 and "adverse" in trade.reason
    assert engine.risk.state.consecutive_losses == 1
    assert engine.repository.attempts(engine.execution.scope)[0].status == "CLOSED"


def test_managed_exit_waits_for_unresolved_partial_request(paper_runtime, broker):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    attempt = engine.repository.attempts(engine.execution.scope)[0]
    payload = json.loads(attempt.payload)
    payload["partial_state"] = "UNKNOWN"
    engine.repository.update_attempt(attempt.key, "OPEN", payload)
    broker.quote.bid = 1.0992
    engine.manage_positions()
    assert len(broker.sent) == 1  # Only the original entry was sent.
    payload = json.loads(engine.repository.attempts(engine.execution.scope)[0].payload)
    assert "adverse" in payload["close_reason"]


def test_live_management_requires_live_permission(paper_runtime, broker):
    engine = paper_runtime
    engine.execution.mode, engine.execution.enable_live = TradingMode.LIVE, True
    engine.process_symbol("EURUSD")
    engine.execution.enable_live = False
    broker.quote.bid = 1.0992
    engine.manage_positions()
    assert len(broker.sent) == 1


@pytest.mark.parametrize("order",[["EURUSD","GBPUSD"],["GBPUSD","EURUSD"]])
def test_cycle_ranks_all_symbols_before_entry_regardless_of_scan_order(paper_runtime,broker,monkeypatch,order):
    engine=paper_runtime
    engine.cfg.symbols["symbols"]["GBPUSD"]={"broker_symbol":"GBPUSD","currencies":["GBP","USD"]}
    calls=[]
    def generate(symbol,*args):
        calls.append("analyze:"+symbol)
        return Signal(symbol,Direction.BUY,.9 if symbol=="GBPUSD" else .8,1.1,1.099,1.103,signal_id=symbol+":one")
    monkeypatch.setattr(engine.signal_engine,"generate",generate)
    execute=engine.execution.execute
    def submit(req):
        calls.append("submit:"+req.symbol)
        return execute(req)
    monkeypatch.setattr(engine.execution,"execute",submit)
    engine.run_cycle(order)
    assert calls[:2]==["analyze:EURUSD","analyze:GBPUSD"]
    assert calls[2]=="submit:GBPUSD"
    assert engine.execution.paper_positions()[0]["symbol"]=="GBPUSD"
    assert not broker.sent


def test_repeated_polling_does_not_inflate_execution_or_funnel_records(paper_runtime,broker):
    engine=paper_runtime
    for _ in range(5):engine.process_symbol("EURUSD")
    assert len(engine.repository.recent_trades())==1
    assert all(row["setups"]==1 for row in engine.repository.decision_funnel(engine.execution.scope))
    decision=engine.repository.get_state(engine.execution.scope+":decision")
    assert decision["direction"]=="HOLD" and decision["reason_code"]=="DUPLICATE"
    assert not broker.sent


def test_risk_rejection_is_final_analysis_decision(paper_runtime,broker):
    engine=paper_runtime
    engine.risk.initialize_state(10000)
    for _ in range(3):engine.risk.register_closed_trade(-1)
    engine.process_symbol("EURUSD")
    decision=engine.repository.get_state("analysis:EURUSD")
    assert decision["direction"]=="HOLD" and decision["reason_code"]=="LOSS_PAUSE"
    assert not broker.sent and not engine.execution.paper_positions()


def test_wait_duration_is_separate_from_opportunity_counts_and_excludes_offline_gap(paper_runtime,broker,monkeypatch):
    from datetime import timedelta
    engine=paper_runtime
    now=[datetime(2026,10,27,12,tzinfo=timezone.utc)]
    engine.risk.clock=lambda:now[0]
    monkeypatch.setattr(engine.feed,"tick_status",lambda *_:{"fresh":False,"reason":"Market closed","status":"MARKET_CLOSED"})
    monkeypatch.setattr(engine.feed,"log_tick_status",lambda *_:None)
    engine.process_symbol("EURUSD")
    now[0]+=timedelta(seconds=2)
    engine.process_symbol("EURUSD")
    now[0]+=timedelta(hours=1)
    engine.process_symbol("EURUSD")
    state=engine.repository.get_state(engine.execution.scope+":wait:availability:EURUSD")
    assert state["totals_seconds"]["MARKET_CLOSED"]==2
    assert engine.repository.decision_funnel(engine.execution.scope)==[]
    assert not broker.sent
