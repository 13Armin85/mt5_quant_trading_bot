from core.execution import ExecutionEngine
from utils.types import Direction, OrderRequest, TradingMode
import pytest
import json
from datetime import datetime, timezone


def request(sid="stable-signal"):
    return OrderRequest("EURUSD", Direction.BUY, .3, 1.1, 1.099, 1.103, 20, signal_id=sid)


class Client:
    def health_check(self): return True


def test_paper_execution_has_no_mt5_order():
    e=ExecutionEngine(Client(),TradingMode.PAPER,False,"does-not-exist.flag")
    r=e.execute(OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.09,1.12,20))
    assert r.success and r.ticket


def test_live_disabled_by_default():
    e=ExecutionEngine(Client(),TradingMode.LIVE,False,"does-not-exist.flag")
    r=e.execute(OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.09,1.12,20))
    assert not r.success


def test_live_checks_broker_and_confirms_stop(live_engine, broker):
    result = live_engine.execute(request())
    assert result.success and result.status == "OPEN"
    assert result.planned_risk <= 100
    assert len(broker.sent) == 1 and broker.sent[0]["type_filling"] == 0


def test_duplicate_survives_restart(live_engine, broker):
    assert live_engine.execute(request()).success
    restarted = ExecutionEngine(broker, TradingMode.LIVE, True, str(live_engine.kill_switch_path), live_engine.risk, live_engine.repo, live_engine.cfg, live_engine.scope)
    assert not restarted.execute(request()).success
    assert len(broker.sent) == 1


@pytest.mark.parametrize("code", [None, 10012, 10031, 10008])
def test_ambiguous_response_never_resends(live_engine, broker, code):
    broker.code = code
    result = live_engine.execute(request())
    assert not result.success and result.status == "UNKNOWN"
    assert not live_engine.execute(request("second")).success
    assert len(broker.sent) == 1
    assert live_engine.repo.attempts(live_engine.scope)[0].status == "UNKNOWN"


def test_rejected_order(live_engine, broker):
    broker.code = 10018
    result = live_engine.execute(request())
    assert not result.success and result.retcode == 10018
    assert live_engine.repo.attempts(live_engine.scope)[0].status == "REJECTED"


def test_partial_fill_is_not_topped_up(live_engine, broker):
    broker.code = 10010
    result = live_engine.execute(request())
    assert result.success and result.status == "PARTIAL" and result.filled_volume == .15
    assert not live_engine.execute(request()).success
    assert len(broker.sent) == 1


def test_missing_protection_emergency_closes_only_bot(live_engine, broker):
    broker.missing_sl = True
    result = live_engine.execute(request())
    assert not result.success and result.status == "PROTECTION_FAILURE"
    assert len(broker.sent) == 2 and broker.sent[1]["position"] == 123
    assert not broker.positions


@pytest.mark.parametrize("field,value", [("sl",1.2),("tp",1.0),("volume",float("nan")),("price",0)])
def test_invalid_requests_do_not_send(live_engine, broker, field, value):
    req = request(); setattr(req, field, value)
    assert not live_engine.execute(req).success and not broker.sent


def test_stale_tick_blocks_send(live_engine, broker):
    broker.quote.time -= 60
    assert "tick" in live_engine.execute(request()).message.lower()
    assert not broker.sent


def test_margin_failure_blocks_send(live_engine, broker):
    broker.margin = 10001
    assert "margin" in live_engine.execute(request()).message.lower()
    assert not broker.sent


def test_spread_and_minimum_stop_block(live_engine, broker):
    broker.quote.bid = 1.09
    assert "Spread" in live_engine.execute(request()).message
    broker.quote.bid = 1.0998
    broker.info.trade_stops_level = 500
    assert "distance" in live_engine.execute(request()).message
    assert not broker.sent


def test_order_check_reject_does_not_send(live_engine, broker):
    broker.check_code = 10019
    assert not live_engine.execute(request()).success and not broker.sent


def test_final_equity_resizes_order(live_engine, broker):
    broker.account.equity = 1000
    result = live_engine.execute(request())
    assert result.success and result.planned_risk <= 10
    assert broker.sent[0]["volume"] < .3


def test_unknown_journal_reconciles_to_position(live_engine, broker):
    assert live_engine.execute(request()).success
    attempt = live_engine.repo.attempts(live_engine.scope)[0]
    live_engine.repo.update_attempt(attempt.key, "UNKNOWN")
    live_engine.reconcile()
    assert live_engine.repo.attempts(live_engine.scope)[0].status == "OPEN"


def test_kill_switch_blocks_entries(live_engine, broker):
    live_engine.kill_switch_path.touch()
    assert not live_engine.execute(request()).success and not broker.sent


def test_send_timeout_exception_is_reconciled_without_resend(live_engine, broker, monkeypatch):
    calls=[]
    def timeout(req):
        calls.append(req)
        raise TimeoutError("simulated timeout")
    monkeypatch.setattr(broker,"order_send",timeout)
    assert live_engine.execute(request()).status=="UNKNOWN"
    assert not live_engine.execute(request("next")).success
    assert len(calls)==1


def test_equity_change_during_check_cannot_exceed_budget(live_engine, broker, monkeypatch):
    def check(req):
        broker.account.equity=100
        return type("Check",(),{"retcode":0})()
    monkeypatch.setattr(broker,"order_check",check)
    assert not live_engine.execute(request()).success
    assert not broker.sent
