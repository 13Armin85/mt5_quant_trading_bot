import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from config import load_config
from signals.strategies import FAMILIES
from backtest.research import generate_signals, compare_strategies
from news.impact import analyze_release, numeric_release
from news.news_provider import NewsEvent
from datetime import datetime, timedelta, timezone


def bars(count=250):
    # Deliberately synthetic unit-test prices; never reported as market performance.
    rng=np.random.default_rng(19)
    close=100 + np.arange(count)*.05 + np.sin(np.arange(count)/4) + rng.normal(0,.1,count)
    return pd.DataFrame({"time":pd.date_range("2026-01-01",periods=count,freq="5min",tz="UTC"),
        "open":close-.05,"high":close+.03,"low":close-.5,"close":close,"tick_volume":np.full(count,100)})


def cfg():
    result=load_config().model_copy(deep=True)
    result.settings["strategy"]["timeframes"]={role:"M5" for role in ("macro","trend","setup","entry")}
    result.settings["trading"]["min_confidence"]=.01
    result.settings["strategy"]["pullback_atr"]=2
    return result


@pytest.mark.parametrize("family",["trend_pullback","ensemble"])
def test_future_prices_do_not_change_prior_signals(family):
    data=bars(270)
    baseline=generate_signals(data.iloc[:250],cfg(),1,family)
    assert len(baseline)>0
    changed=data.copy()
    for column in ("open","high","low","close"):
        changed.loc[250:,column] *= 2
    full=generate_signals(changed,cfg(),1,family)
    prior=full[full.time < data.time.iloc[250]].reset_index(drop=True)
    assert_frame_equal(baseline.reset_index(drop=True),prior)


def test_comparison_reports_all_families_and_splits():
    report=compare_strategies(bars(225),cfg(),1,{"commission_per_lot":.01})
    assert set(report["families"])=={*FAMILIES,"ensemble"}
    assert report["validation_choice"] is None
    assert not report["news_validated"]
    for result in report["families"].values():
        assert {"train","validation","test","cost_sensitivity","parameter_sensitivity","walk_forward"} <= result.keys()


def test_numeric_surprise_requires_units_and_explicit_rule():
    now=datetime.now(timezone.utc)
    event=NewsEvent(now-timedelta(minutes=1),"USD","MEDIUM","Unemployment rate","4.2%","4.0%","3.9%",unit="%")
    result=analyze_release(event,[{"contains":"unemployment","higher_is_positive":False}],now)
    assert result["surprise"] > 0 and result["currency_bias"]==-1
    event.forecast="4.0K"
    assert analyze_release(event,[],now)["currency_bias"] is None
    assert numeric_release("123K")== (123000,"NUMBER")


def test_unpublished_release_never_gets_directional_bias():
    now=datetime.now(timezone.utc)
    event=NewsEvent(now+timedelta(minutes=1),"USD","HIGH","Interest rate","5%","4%")
    assert analyze_release(event,[{"contains":"interest","higher_is_positive":True}],now)["currency_bias"] is None
