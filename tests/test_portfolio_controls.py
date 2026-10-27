from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import numpy as np
import pandas as pd
import pytest

from config import load_config
from core.correlation import CorrelationGuard
from core.risk_manager import RiskManager, RiskState
from core.symbol_policy import trading_policy
from database.repository import Repository


def guard():
    return CorrelationGuard(symbols=load_config().symbols)


def test_news_usd_tag_does_not_equate_gold_fx_and_indices():
    g = guard()
    assert g.can_open("XAUUSD_o",[("EURUSD_o","BUY")],pd.DataFrame())[0]
    assert g.can_open("NQ",[("EURUSD_o","BUY")],pd.DataFrame())[0]
    assert not g.can_open("YM",[("NQ","BUY")],pd.DataFrame())[0]


def test_currency_factor_direction_and_unknown_mapping():
    g = guard()
    assert not g.can_open("GBPUSD_o",[("EURUSD_o","BUY")],pd.DataFrame(),"BUY")[0]
    assert g.can_open("USDJPY_o",[("EURUSD_o","BUY")],pd.DataFrame(),"BUY")[0]
    assert not g.can_open("USDJPY_o",[("EURUSD_o","SELL")],pd.DataFrame(),"BUY")[0]
    assert not g.can_open("XAUUSD_o",[("unknown","BUY")],pd.DataFrame())[0]


def test_correlation_pnl_signs_and_minimum_aligned_history():
    g = guard()
    t = pd.date_range("2026-01-01",periods=100,freq="h",tz="UTC")
    rng = np.random.default_rng(3)
    prices = pd.Series(100*np.cumprod(1+rng.normal(0,.001,len(t))),index=t)
    matrix = g.matrix({"XAUUSD_o":prices,"EURUSD_o":prices*2})
    assert not g.can_open("XAUUSD_o",[("EURUSD_o","BUY")],matrix,"BUY")[0]
    assert g.can_open("XAUUSD_o",[("EURUSD_o","BUY")],matrix,"SELL")[0]
    short = g.matrix({"XAUUSD_o":prices.iloc[:20],"EURUSD_o":prices.iloc[:20]})
    assert pd.isna(short.loc["XAUUSD_o","EURUSD_o"])
    misaligned = g.matrix({"XAUUSD_o":prices,"EURUSD_o":prices.set_axis(t+pd.Timedelta(minutes=30))})
    assert pd.isna(misaligned.loc["XAUUSD_o","EURUSD_o"])


def test_pause_survives_restart_and_week_rollover_without_silent_live_reset(risk_cfg):
    now = [datetime(2026,10,25,23,30,tzinfo=timezone.utc)]
    risk = RiskManager(risk_cfg,clock=lambda:now[0])
    risk.initialize_state(10000)
    for _ in range(3): risk.register_closed_trade(-1)
    restored = RiskManager(risk_cfg,clock=lambda:now[0])
    restored.state = RiskState(**asdict(risk.state))
    now[0] += timedelta(days=2)
    assert not restored.can_trade(10000)[0]
    assert restored.state.status=="PAUSED" and restored.state.resume_at is None


def test_paper_resume_does_not_bypass_weekly_loss_or_kill_switch(risk_cfg):
    now = [datetime(2026,10,27,23,30,tzinfo=timezone.utc)]
    cfg = {**risk_cfg,"paper_loss_pause_resume":"next_day"}
    risk = RiskManager(cfg,clock=lambda:now[0],mode="PAPER")
    risk.initialize_state(10000)
    for _ in range(3): risk.register_closed_trade(-1)
    assert risk.state.resume_at=="2026-10-28T00:00:00+00:00"
    now[0] += timedelta(hours=1)
    assert risk.can_trade(10000)[0]
    assert risk.state.last_resume_at and risk.state.status=="ACTIVE"
    assert not risk.can_trade(9300)[0]
    for _ in range(3): risk.register_closed_trade(-1)
    assert not risk.request_resume("first",10000,kill_switch=True)[0]
    assert not risk.request_resume("first",10000)[0]
    assert not risk.request_resume("second",9300)[0]
    assert risk.request_resume("third",10000)[0]


def test_remaining_total_cap_applies_even_to_opposing_factor_positions(broker,risk_cfg):
    risk = RiskManager({**risk_cfg,"risk_per_trade":.005},broker)
    volume = risk.calculate_volume("USDJPY",1.1,1.099,10000,broker.info,open_risk=90)
    assert volume*risk.loss_per_lot("USDJPY",1.1,1.099,broker.info)+90<=100


def test_per_symbol_costs_override_without_news_mapping(broker,risk_cfg):
    symbols = {"symbols":{"EURUSD":{"costs":{"commission_per_lot":50,"slippage_points":50}}}}
    risk = RiskManager(risk_cfg,broker,symbols=symbols)
    assert risk.loss_per_lot("EURUSD",1.1,1.099,broker.info)>RiskManager(risk_cfg,broker).loss_per_lot("EURUSD",1.1,1.099,broker.info)
    assert trading_policy("XAUUSD",risk_cfg,symbols)==risk_cfg


@pytest.mark.parametrize("costs",[{"slippage_points":-1},{"point":.001},{"commission_per_lot":float("nan")},{"max_spread_points":"40"}])
def test_invalid_symbol_costs_rejected(costs):
    cfg = load_config().model_copy(deep=True)
    cfg.symbols["symbols"]["EURUSD"]["costs"] = costs
    with pytest.raises(ValueError):cfg.validate_runtime()


def test_atomic_live_settlement_restores_exactly_once_loss_state(tmp_path,risk_cfg):
    repo = Repository("sqlite:///"+str(tmp_path/"journal.db"))
    risk = RiskManager(risk_cfg);risk.initialize_state(10000)
    repo.claim_execution("live:one",{})
    state = risk.closed_state(-20)
    assert repo.settle_live("live:one","live",123,1.1,-20,state)
    assert not repo.settle_live("live:one","live",123,1.1,-20,risk.closed_state(-20))
    restarted = RiskState(**repo.get_state("live:risk"))
    assert restarted.consecutive_losses==1


def test_atomic_paper_settlement_includes_pause_state(tmp_path,risk_cfg):
    repo = Repository("sqlite:///"+str(tmp_path/"paper.db"))
    risk=RiskManager(risk_cfg);risk.initialize_state(10000)
    risk.state.consecutive_losses=2
    repo.claim_execution("paper:one",{})
    repo.update_attempt("paper:one","PAPER_OPEN")
    state=risk.closed_state(-20)
    assert repo.settle_paper("paper:one","paper",123,1.1,-20,risk_state=state)
    assert not repo.settle_paper("paper:one","paper",123,1.1,-20,risk_state=state)
    assert repo.get_state("paper:risk")["status"]=="PAUSED"
    assert repo.get_state("paper:paper_pnl")==-20


def test_funnel_deduplicates_polls_and_restarts(tmp_path):
    url="sqlite:///"+str(tmp_path/"funnel.db")
    decision=dict(evaluation_id="one",symbol="EURUSD",strategy="trend",regime="RANGING",session="London",stage="risk",reason_code="TOTAL_RISK")
    repo=Repository(url)
    for _ in range(20):repo.record_decision("paper",decision)
    repo=Repository(url);repo.record_decision("paper",decision)
    assert repo.decision_funnel("paper")[0]["setups"]==1
