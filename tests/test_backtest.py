import pandas as pd
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics
import pytest


def data():
    return pd.DataFrame({"time":pd.date_range("2026-01-01",periods=4,freq="h",tz="UTC"),"open":[100,100,100,100],"high":[100,101,103,101],"low":[100,99.5,98,99.5],"close":[100,100,100,100]})


def signals(bars):
    return pd.DataFrame([{"time":bars.time.iloc[0],"direction":"BUY","entry":100,"sl":99,"tp":102,"contract_value_per_price":100,"volume":100}])


def test_backtest_tp():
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=4,freq="h",tz="UTC"),"open":[100,100,102,103],"high":[100,103,104,104],"low":[100,99.5,101,102],"close":[100,102,103,103]})
    signals=pd.DataFrame([{"time":bars.time.iloc[0],"direction":"BUY","entry":100,"sl":99,"tp":102,"contract_value_per_price":1,"volume":1}])
    trades,metrics=BacktestEngine().run(bars,signals)
    assert len(trades)==1 and trades.iloc[0].outcome=="TP" and metrics["total_trades"]==1


def test_collision_is_stop_first_with_costs_and_risk_cap():
    bars=data()
    trades,_=BacktestEngine(spread=.1,slippage=.01,commission_per_lot=7).run(bars,signals(bars))
    assert trades.iloc[0].outcome=="SL" and trades.iloc[0].profit < 0
    assert trades.iloc[0].risk <= 100


def test_next_bar_open_ignores_signal_entry_price():
    bars=data().astype({"open":float,"high":float,"low":float,"close":float}); bars.loc[1,["open","high","low","close"]]=[100.5,101,100,100.5]
    ss=signals(bars); ss["entry"]=50
    trades,_=BacktestEngine().run(bars,ss)
    assert trades.iloc[0].entry==100.5 and trades.iloc[0].entry_time==bars.time.iloc[1]


def test_gap_stop_uses_worse_price():
    bars=data(); bars.loc[2,["open","high","low","close"]]=[95,96,94,95]
    trades,_=BacktestEngine().run(bars,signals(bars))
    assert trades.iloc[0]["exit"]==95 and trades.iloc[0].profit < -100


def test_open_trade_is_liquidated_at_end():
    bars=data(); ss=signals(bars); ss["sl"]=90;ss["tp"]=120
    trades,_=BacktestEngine().run(bars,ss)
    assert trades.iloc[0].outcome=="END_OF_DATA"


def test_overlapping_signals_share_one_percent_budget():
    bars=data(); ss=signals(bars)
    second=ss.copy(); second["time"]=bars.time.iloc[1]
    engine=BacktestEngine()
    trades,_=engine.run(bars,pd.concat([ss,second]))
    assert len(trades)==1
    assert "aggregate" in engine.rejections[0]["reason"]


def test_first_loss_counts_in_drawdown():
    metrics=calculate_metrics(pd.DataFrame({"profit":[-100,-100,50]}),10000)
    assert metrics["max_drawdown"]==pytest.approx(.02)
    assert metrics["net_return"]==pytest.approx(-.015)


def test_revised_future_news_rejected():
    bars=data(); ss=signals(bars); ss["news_available_at"]=bars.time.iloc[1]
    with pytest.raises(ValueError,match="Future news"):
        BacktestEngine().run(bars,ss)


def test_unsorted_bars_rejected():
    bars=data().iloc[::-1]
    with pytest.raises(ValueError,match="ordered"):
        BacktestEngine().run(bars,signals(data()))
