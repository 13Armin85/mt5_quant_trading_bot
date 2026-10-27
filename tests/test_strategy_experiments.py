import pandas as pd
from signals.strategies import breakout_retest
from utils.types import Direction


def breakout_bars():
    return pd.DataFrame({"time":pd.date_range("2026-01-01",periods=5,freq="5min",tz="UTC"),
        "open":[99.,99.,99.,100.5,101.],"high":[100.,100.,102.,102.,102.],
        "low":[98.,98.,98.,99.9,100.1],"close":[99.,99.,101.,101.5,101.5],"atr":[1.]*5})


def test_retest_state_confirms_first_touch_then_consumes_and_is_prefix_invariant():
    bars=breakout_bars()
    first=breakout_retest(bars.iloc[:4],2,expiry=3)
    assert first[0]==Direction.BUY
    assert breakout_retest(bars,2,expiry=3)[0]==Direction.HOLD
    bars.loc[4,["high","low","close"]]=[200.,1.,100.]
    assert breakout_retest(bars.iloc[:4],2,expiry=3)==first


def test_retest_invalidates_after_close_through_level():
    bars=breakout_bars();bars.loc[3,"close"]=99.
    assert breakout_retest(bars.iloc[:4],2,expiry=3)[0]==Direction.HOLD
