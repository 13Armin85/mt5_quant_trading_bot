import pandas as pd
import pytest
from backtest.engine import BacktestEngine


def bars(count=5):
    return pd.DataFrame({"time":pd.date_range("2026-01-01",periods=count,freq="h",tz="UTC"),
        "open":[100.]*count,"high":[100.5]*count,"low":[99.5]*count,"close":[100.]*count})


def proposal(data,symbol="__single__",**overrides):
    return {"time":data.time.iloc[0],"symbol":symbol,"direction":"BUY","sl":90.,"tp":130.,
        "contract_value_per_price":1.,**overrides}


def test_total_risk_distinct_from_per_trade_and_order_invariance():
    data=bars()
    joined=pd.concat([data.assign(symbol="B"),data.assign(symbol="A")]).sort_values("time",kind="stable")
    proposals=pd.DataFrame([proposal(data,"B"),proposal(data,"A")])
    test=BacktestEngine(risk_fraction=.005,max_total_risk=.01)
    trades,metrics=test.run(joined,proposals)
    reverse,_=BacktestEngine(risk_fraction=.005,max_total_risk=.01).run(joined,proposals.iloc[::-1])
    assert len(trades)==2 and metrics["max_concurrent_positions"]==2
    assert trades.risk.sum()<=100
    pd.testing.assert_frame_equal(trades,reverse)


def test_marked_drawdown_captures_floating_loss_before_recovery():
    data=bars();data.loc[2,["open","high","low","close"]]=[100,100,95,95]
    test=BacktestEngine()
    _,metrics=test.run(data,pd.DataFrame([proposal(data)]))
    assert metrics["max_drawdown"]>metrics["realized_max_drawdown"]
    assert not metrics["live_ready"] and "Bar-close" in metrics["drawdown_basis"]


def test_break_even_applies_next_bar_and_does_not_use_prior_bar_low():
    data=bars(4)
    data.loc[1,["open","high","low","close"]]=[100,111,99,111]
    data.loc[2,["open","high","low","close"]]=[111,112,101,111]
    data.loc[3,["open","high","low","close"]]=[111,112,99,100]
    test=BacktestEngine(position_management={"break_even_enabled":True})
    trades,_=test.run(data,pd.DataFrame([proposal(data)]))
    assert trades.iloc[0].exit_time==data.time.iloc[3] and trades.iloc[0]["exit"]==100


def test_partial_and_commission_are_not_double_counted():
    data=bars(4)
    data.loc[1,["open","high","low","close"]]=[100,111,99,111]
    data.loc[2:,["open","high","low","close"]]=[111,112,110,111]
    trades,_=BacktestEngine(commission_per_lot=1,position_management={"partial_close_enabled":True,
        "partial_close_r":1,"partial_close_fraction":.5}).run(data,pd.DataFrame([proposal(data,volume=2)]))
    assert trades.iloc[0].profit==pytest.approx(20)


def test_collision_is_reported_and_trailing_never_applies_retroactively():
    data=bars();data.loc[1,["high","low"]]=[131,89]
    trades,metrics=BacktestEngine().run(data,pd.DataFrame([proposal(data)]))
    assert metrics["ambiguous_bars"]==1 and trades.iloc[0].ambiguous


def test_daily_trade_cap_and_same_symbol_reentry_block():
    data=bars()
    first=proposal(data)
    second=proposal(data,time=data.time.iloc[1])
    test=BacktestEngine(risk_fraction=.002)
    trades,_=test.run(data,pd.DataFrame([first,second]))
    assert len(trades)==1 and test.rejections[0]["reason_code"]=="DUPLICATE"


def test_buy_sell_tick_grid_and_minimum_volume():
    data=bars()
    test=BacktestEngine(tick_size=.25)
    _,metrics=test.run(data,pd.DataFrame([proposal(data,sl=90.1)]))
    assert metrics["total_trades"]==0 and test.rejections[0]["reason_code"]=="STRUCTURE"
    trades,_=BacktestEngine(volume_min=100).run(data,pd.DataFrame([proposal(data)]))
    assert trades.empty


def test_explicit_available_time_prevents_early_entry():
    data=bars()
    trades,_=BacktestEngine().run(data,pd.DataFrame([proposal(data,available_at=data.time.iloc[3])]))
    assert trades.iloc[0].entry_time==data.time.iloc[3]


def test_portfolio_backtest_uses_independent_factors_and_blocks_unknown_mapping():
    from config import load_config
    data=bars()
    joined=pd.concat([data.assign(symbol="EURUSD_o"),data.assign(symbol="XAUUSD_o")]).sort_values("time",kind="stable")
    proposals=pd.DataFrame([proposal(data,"EURUSD_o"),proposal(data,"XAUUSD_o")])
    trades,_=BacktestEngine(risk_fraction=.005,symbols=load_config().symbols).run(joined,proposals)
    assert len(trades)==2
    unknown=bars().assign(symbol="unknown")
    engine=BacktestEngine(symbols=load_config().symbols)
    trades,_=engine.run(unknown,pd.DataFrame([proposal(data,"unknown")]))
    assert trades.empty and engine.rejections[0]["reason_code"]=="EXPOSURE"
