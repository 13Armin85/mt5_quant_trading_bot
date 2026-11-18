from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from config import load_config
from core.account_login import AccountError
from core.account_portal import DesktopRuntime, AccountPortal
from core.risk_manager import RiskManager
from core.trading_preferences import DEFAULTS, DailyGuard, activity_config, validate_preferences, read_preferences, write_preferences, preferences_path
from database.repository import Repository


def policy(**values):
    return {**DEFAULTS, "daily_enabled":True, **values}


@pytest.mark.parametrize("bad", [None, {}, {**DEFAULTS, "scope":"other"},
    {**DEFAULTS,"loss_limit":float("nan")}, {**DEFAULTS,"profit_target":float("inf")},
    {**DEFAULTS,"loss_limit":0}, {**DEFAULTS,"loss_limit":-2}, {**DEFAULTS,"loss_limit":True},
    {**DEFAULTS,"daily_enabled":1}, {**DEFAULTS,"activity":"unlimited"}])
def test_preferences_reject_unbounded_invalid_values_and_scope_injection(bad):
    with pytest.raises(ValueError): validate_preferences(bad)


def test_daily_guard_latches_survives_restart_and_uses_tehran_midnight(tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path/"daily.db"))
    now = [datetime(2026,11,18,20,29,tzinfo=timezone.utc)]
    guard = DailyGuard(repo,"demo:one",lambda:now[0])
    guard.policy = policy()
    assert guard.update(300)["day"] == "2026-11-18"
    assert guard.remaining(299) == 1
    assert guard.update(298)["halt"] == "DAILY_LOSS_AMOUNT"
    restarted = DailyGuard(repo,"demo:one",lambda:now[0])
    restarted.policy = policy(profit_target=10,loss_limit=3)
    assert restarted.update(300)["halt"] == "DAILY_LOSS_AMOUNT"
    assert restarted.remaining(300) == 0
    now[0] += timedelta(minutes=1)
    rolled = restarted.update(300)
    assert rolled["day"] == "2026-11-19" and rolled["halt"] is None
    assert rolled["baseline_equity"] == 300


def test_target_latch_never_resets_on_equity_fall_or_policy_save(tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path/"daily.db"))
    guard = DailyGuard(repo,"one",lambda:datetime.now(timezone.utc))
    guard.policy = policy()
    guard.update(300)
    assert guard.allowed(305) == (False,"DAILY_PROFIT_TARGET")
    guard.policy = policy(profit_target=8)
    assert guard.allowed(303) == (False,"DAILY_PROFIT_TARGET")
    other = DailyGuard(repo,"other",lambda:datetime.now(timezone.utc))
    assert not other.state


def test_daily_guard_caps_new_risk_including_existing_exposure(broker, risk_cfg, tmp_path):
    repo = Repository("sqlite:///" + str(tmp_path/"daily.db"))
    risk = RiskManager(risk_cfg,broker)
    risk.daily_guard = DailyGuard(repo,"one",risk.clock)
    risk.daily_guard.policy = policy()
    risk.daily_guard.update(10000)
    volume = risk.calculate_volume("EURUSD",1.1,1.099,10000,broker.info)
    assert 0 < risk.loss_per_lot("EURUSD",1.1,1.099,broker.info)*volume <= 2
    assert risk.calculate_volume("EURUSD",1.1,1.099,10000,broker.info,open_risk=1.9) == 0
    assert risk.can_trade(9998)[0] is False


def test_active_mode_shortens_entry_without_raising_risk_or_disabling_guards():
    cfg=load_config()
    active=activity_config(cfg,"active")
    assert active.get("strategy.timeframes.entry") == "M1"
    assert active.get("trading.max_trades_per_day") == 24
    assert active.get("trading.min_confidence") == .65
    for field in ("risk_per_trade","max_total_risk","max_daily_loss","max_consecutive_losses","max_positions","min_rr"):
        assert active.get("trading."+field) == cfg.get("trading."+field)
    assert active.get("news") == cfg.get("news")
    assert cfg.get("strategy.timeframes.entry") == "M5"
    active.validate_runtime()


def test_authenticated_settings_are_scoped_and_do_not_restart_stopped_engine(tmp_path,monkeypatch):
    cfg=load_config().model_copy(deep=True)
    cfg.env.database_url="sqlite:///"+str(tmp_path/"daily.db")
    runtime=DesktopRuntime(cfg,root=tmp_path)
    runtime.account={"login":12345,"server":"LiteFinance-MT5-Demo"}
    monkeypatch.setattr(runtime,"compose",lambda *a,**k:pytest.fail("Settings must not start/stop trading"))
    runtime.stopped_flag().parent.mkdir(parents=True)
    runtime.stopped_flag().touch()
    portal=AccountPortal("x"*40,runtime)
    portal.sessions["a"*30]=( __import__('time').monotonic(),runtime)
    with pytest.raises(AccountError): portal.dispatch('/preferences',policy(),None)
    result=portal.dispatch('/preferences',policy(),'a'*30)
    assert result['ok'] and runtime.stopped_flag().exists()
    assert read_preferences(runtime.stopped_flag().parent,runtime.scope()) == policy()
    assert not preferences_path(runtime.stopped_flag().parent,'other').exists()


def test_preferences_use_private_atomic_file_without_writing_trade_journal(tmp_path,monkeypatch):
    monkeypatch.setattr(Repository,'set_state',lambda *a:pytest.fail('Portal must not write the trade journal'))
    assert write_preferences(tmp_path,'one',policy()) == policy()
    assert read_preferences(tmp_path,'one') == policy()
    write_preferences(tmp_path,'one',policy(profit_target=8))
    assert read_preferences(tmp_path,'one')['profit_target'] == 8
    assert read_preferences(tmp_path,'other') == DEFAULTS
    assert not list(tmp_path.glob('.preferences-*'))
    assert len(list(tmp_path.glob('preferences-*.json'))) == 1


def test_final_execution_rechecks_remaining_daily_budget(live_engine,broker):
    from utils.types import Direction,OrderRequest
    guard=DailyGuard(live_engine.repo,live_engine.scope,live_engine.risk.clock)
    guard.policy=policy()
    guard.update(10000)
    live_engine.risk.daily_guard=guard
    req=OrderRequest('EURUSD',Direction.BUY,1,1.1,1.099,1.104,20,signal_id='daily-budget')
    result=live_engine.execute(req)
    assert result.success and result.planned_risk <= 2
    assert broker.sent[0]['volume'] <= .01
