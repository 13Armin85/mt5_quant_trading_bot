from types import SimpleNamespace
from core.risk_manager import RiskManager
import pytest


def info(tick_size, tick_value, min_v=.01, max_v=100, step=.01):
    return SimpleNamespace(trade_tick_size=tick_size, point=tick_size, trade_tick_value_loss=tick_value, trade_tick_value=tick_value, volume_min=min_v, volume_max=max_v, volume_step=step)


def test_risk_amount_one_percent():
    r=RiskManager({"risk_per_trade":.01,"max_daily_loss":.03,"max_weekly_loss":.06,"max_consecutive_losses":3,"max_total_risk":.03})
    assert r.risk_amount(10000)==100


def test_volume_respects_theoretical_loss_eurusd():
    r=RiskManager({"risk_per_trade":.01,"max_daily_loss":.03,"max_weekly_loss":.06,"max_consecutive_losses":3,"max_total_risk":.03})
    i=info(.00001,1.0)
    v=r.calculate_volume("EURUSD",1.10000,1.09500,10000,i)
    assert 0 < r.theoretical_loss(1.1,1.095,v,i) <= 100.01


def test_volume_respects_theoretical_loss_gold():
    r=RiskManager({"risk_per_trade":.01,"max_daily_loss":.03,"max_weekly_loss":.06,"max_consecutive_losses":3,"max_total_risk":.03})
    i=info(.01,1.0)
    v=r.calculate_volume("XAUUSD",2400,2390,10000,i)
    assert 0 < r.theoretical_loss(2400,2390,v,i) <= 100.01


def test_daily_lock():
    r=RiskManager({"risk_per_trade":.01,"max_daily_loss":.03,"max_weekly_loss":.06,"max_consecutive_losses":3,"max_total_risk":.03})
    r.initialize_state(10000)
    assert not r.daily_locked(9800)
    assert r.daily_locked(9700)


def test_broker_conversion_costs_and_remaining_budget(broker, risk_cfg):
    risk = RiskManager(risk_cfg, broker)
    volume = risk.calculate_volume("EURUSD", 1.1, 1.099, 10000, broker.info, open_risk=60)
    loss = risk.loss_per_lot("EURUSD", 1.1, 1.099, broker.info) * volume
    assert 0 < loss <= 40 and loss + 60 <= 100


def test_minimum_volume_rejected(broker, risk_cfg):
    broker.info.volume_min = 1
    assert RiskManager(risk_cfg, broker).calculate_volume("EURUSD",1.1,1.09,10000,broker.info) == 0


@pytest.mark.parametrize("step", [.01,.1,.25])
def test_volume_floors_to_broker_step(step):
    volume = RiskManager._floor_step(.7499,step)
    assert volume <= .7499
    assert abs(volume / step - round(volume / step)) < 1e-9


def test_unknown_existing_risk_blocks(broker, risk_cfg):
    broker.positions = [SimpleNamespace(symbol="EURUSD",sl=0,volume=.1)]
    with pytest.raises(ValueError, match="SL"):
        RiskManager(risk_cfg, broker).account_exposure()


def test_pending_orders_use_account_currency(broker, risk_cfg):
    broker.orders = [SimpleNamespace(symbol="EURUSD",sl=1.09,volume_current=.1,type=2,price_open=1.1)]
    exposure, _, _ = RiskManager(risk_cfg, broker).account_exposure()
    assert exposure > 100


def test_failed_profit_calculation_blocks(broker, risk_cfg, monkeypatch):
    monkeypatch.setattr(broker,"order_calc_profit",lambda *args:None)
    with pytest.raises(ValueError, match="calculation"):
        RiskManager(risk_cfg,broker).calculate_volume("EURUSD",1.1,1.09,10000,broker.info)


@pytest.mark.parametrize("equity", [0,-1,float("nan"),float("inf")])
def test_invalid_equity_rejected(equity, risk_cfg):
    with pytest.raises(ValueError):
        RiskManager(risk_cfg).risk_amount(equity)
