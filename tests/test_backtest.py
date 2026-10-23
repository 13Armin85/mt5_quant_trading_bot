import pandas as pd
from backtest.engine import BacktestEngine


def test_backtest_tp():
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=4,freq="h",tz="UTC"),"open":[100]*4,"high":[100,103,104,104],"low":[100,99.5,101,102],"close":[100,102,103,103]})
    signals=pd.DataFrame([{"time":bars.time.iloc[0],"direction":"BUY","entry":100,"sl":99,"tp":102,"contract_value_per_price":1,"volume":1}])
    trades,metrics=BacktestEngine().run(bars,signals)
    assert len(trades)==1 and trades.iloc[0].outcome=="TP" and metrics["total_trades"]==1
