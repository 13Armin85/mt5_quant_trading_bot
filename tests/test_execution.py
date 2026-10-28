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


def test_live_uses_configured_strong_setup_risk_cap(live_engine, broker):
    live_engine.risk.cfg["risk_per_trade"] = .005
    live_engine.risk.cfg["quality_sizing"] = {"enabled": True, "risk_per_trade": .009}
    req = request("strong-setup")
    req.volume = 1.0
    req.tp = 1.104
    req.risk_fraction = .009
    req.minimum_net_rr = 2.0
    result = live_engine.execute(req)
    assert result.success and 50 < result.planned_risk <= 90
    assert broker.sent[0]["volume"] == result.filled_volume
    payload = json.loads(live_engine.repo.attempts(live_engine.scope)[0].payload)
    assert payload["risk_fraction"] == .009


def test_live_rejects_risk_above_configured_ceiling(live_engine, broker):
    live_engine.risk.cfg["risk_per_trade"] = .005
    live_engine.risk.cfg["quality_sizing"] = {"enabled": True, "risk_per_trade": .009}
    req = request("too-much-risk")
    req.risk_fraction = .01
    assert "ceiling" in live_engine.execute(req).message
    assert not broker.sent


def test_strong_setup_rechecks_higher_net_reward_risk_before_send(live_engine, broker):
    live_engine.risk.cfg["risk_per_trade"] = .005
    live_engine.risk.cfg["quality_sizing"] = {"enabled": True, "risk_per_trade": .009}
    req = request("strong-rr-too-low")
    req.risk_fraction = .009
    req.minimum_net_rr = 2.0
    assert "net reward/risk" in live_engine.execute(req).message
    assert not broker.sent


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


def test_unsent_order_check_rejection_can_retry_after_restart(live_engine,broker):
    broker.check_code=10019
    assert not live_engine.execute(request()).success and not broker.sent
    broker.check_code=0
    restarted=ExecutionEngine(broker,TradingMode.LIVE,True,str(live_engine.kill_switch_path),
        live_engine.risk,live_engine.repo,live_engine.cfg,live_engine.scope)
    assert restarted.execute(request()).success
    assert len(broker.sent)==1


def test_actual_rejected_send_retains_permanent_claim(live_engine,broker):
    broker.code=10018
    assert not live_engine.execute(request()).success
    broker.code=10009
    assert not live_engine.execute(request()).success
    assert len(broker.sent)==1


def test_ambiguous_emergency_close_is_not_resent(live_engine,broker,monkeypatch):
    broker.missing_sl=True
    send=broker.order_send
    def uncertain(req):
        if "position" in req:
            broker.sent.append(req.copy())
            return None
        return send(req)
    monkeypatch.setattr(broker,"order_send",uncertain)
    assert live_engine.execute(request()).status=="PROTECTION_FAILURE"
    live_engine.reconcile()
    live_engine.reconcile()
    assert len(broker.sent)==2
    attempt=live_engine.repo.attempts(live_engine.scope)[0]
    assert json.loads(attempt.payload)["close_state"]=="UNKNOWN"


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


def test_small_quote_change_rechecks_and_keeps_original_risk_ceiling(live_engine, broker, monkeypatch):
    checks=[]
    original=broker.order_check
    def check(req):
        checks.append(req.copy())
        if len(checks)==1:
            broker.quote.ask += .00005
            broker.quote.bid += .00005
        return original(req)
    monkeypatch.setattr(broker,"order_check",check)
    result=live_engine.execute(request())
    assert result.success and len(checks)==2 and len(broker.sent)==1
    assert broker.sent[0]["price"]==checks[1]["price"]==pytest.approx(1.10005)
    assert broker.sent[0]["volume"]<=checks[0]["volume"]
    assert result.planned_risk<=100


def test_sell_quote_change_uses_bid_and_same_risk_ceiling(live_engine, broker, monkeypatch):
    checks=[]
    original=broker.order_check
    def check(req):
        checks.append(req.copy())
        if len(checks)==1:
            broker.quote.bid -= .00005
            broker.quote.ask -= .00005
        return original(req)
    monkeypatch.setattr(broker,"order_check",check)
    req=OrderRequest("EURUSD",Direction.SELL,.3,1.0998,1.101,1.096,20,signal_id="sell-reprice")
    result=live_engine.execute(req)
    assert result.success and len(checks)==2 and len(broker.sent)==1
    assert broker.sent[0]["type"]==1 and broker.sent[0]["price"]==pytest.approx(1.09975)
    assert result.planned_risk<=100


def test_large_quote_change_abstains_without_send(live_engine, broker, monkeypatch):
    original=broker.order_check
    def check(req):
        broker.quote.ask += .00025
        broker.quote.bid += .00025
        return original(req)
    monkeypatch.setattr(broker,"order_check",check)
    result=live_engine.execute(request())
    assert not result.success and "deviation" in result.message
    assert not broker.sent


def test_second_check_quote_change_abstains_without_send(live_engine, broker, monkeypatch):
    original=broker.order_check
    calls=[]
    def check(req):
        calls.append(req.copy())
        broker.quote.ask += .00001
        broker.quote.bid += .00001
        return original(req)
    monkeypatch.setattr(broker,"order_check",check)
    result=live_engine.execute(request())
    assert len(calls)==2 and not result.success and not broker.sent
