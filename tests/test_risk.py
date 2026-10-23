from types import SimpleNamespace
from core.risk_manager import RiskManager


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
