import json
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from config import load_config
from analysis.indicators import add_indicators
from analysis.regime_detector import detect_regime
from signals.strategies import FAMILIES, GROUPS, strategy_direction
from signals.selector import Assessment, select_assessments, select_strategy, indicator_snapshot
from signals.signal_engine import SignalEngine
from utils.types import Direction, MarketRegime


def frames_for(family):
    data={"time":pd.date_range("2026-01-01",periods=32,freq="5min",tz="UTC"),"open":100.,"high":100.2,"low":99.8,"close":100.,
          "atr":1.,"ema_20":100.1,"ema_50":99.,"ema_200":98.,"rsi":55.,"relative_volume":1.2,
          "macd":.1,"macd_signal":0.,"macd_hist":.1,"bb_low":99.,"bb_high":101.,
          "cloud_a":99.,"cloud_b":100.,"tenkan":100.,"kijun":99.5,"supertrend_direction":1.,
          "stoch_k":25.,"stoch_d":20.,"cci":-80.,"keltner_high":100.4,"keltner_low":99.6,"vwap":100.1,"obv":100.,"adx":30.,"adx_pos":30.,"adx_neg":10.}
    entry=pd.DataFrame(data)
    entry.loc[31,["close","high","obv"]]=[100.5,100.7,110.]
    if family=="mean_reversion":
        entry.loc[30,["open","close","high","low","rsi"]]=[98.1,98.,98.2,97.8,25.]
    if family=="ema_cross":
        entry.loc[30,"ema_20"]=98.5
        entry.loc[31,"ema_20"]=99.5
    if family=="macd_cross": entry.loc[30,"macd"]=-.1
    if family=="supertrend": entry.loc[30,"supertrend_direction"]=-1
    if family=="stochastic_reversal": entry.loc[30,["stoch_k","stoch_d"]]=[10.,15.]
    if family=="cci_reversion": entry.loc[30,"cci"]=-120.
    higher=entry.copy()
    higher[["ema_20","ema_50","ema_200"]]=[99.,98.,97.]
    higher["rsi"]=60.
    return {"entry":entry,"setup":higher.copy(),"trend":higher.copy(),"macro":higher.copy()}


def mirror(frames):
    result={}
    price_columns=("open","close","ema_20","ema_50","ema_200","tenkan","kijun","vwap")
    pairs=(("high","low"),("bb_high","bb_low"),("keltner_high","keltner_low"),("cloud_a","cloud_b"))
    for role,original in frames.items():
        df=original.copy()
        for column in price_columns: df[column]=200-original[column]
        for a,b in pairs:
            df[a],df[b]=200-original[b],200-original[a]
        for column in ("rsi","stoch_k","stoch_d"): df[column]=100-original[column]
        for column in ("macd","macd_signal","macd_hist","supertrend_direction","cci","obv"): df[column]=-original[column]
        result[role]=df
    return result


@pytest.mark.parametrize("family",FAMILIES)
@pytest.mark.parametrize("side",[Direction.BUY,Direction.SELL])
def test_every_strategy_has_causal_buy_and_sell_rules(family,side):
    frames=frames_for(family)
    regime=MarketRegime.RANGING if GROUPS[family]=="reversion" else MarketRegime.TRENDING_BULLISH
    if side==Direction.SELL:
        frames=mirror(frames)
        if regime!=MarketRegime.RANGING: regime=MarketRegime.TRENDING_BEARISH
    actual,_,reason=strategy_direction(frames,regime,family)
    assert actual==side,(family,reason)


def assessment(name,group,direction=Direction.BUY,score=.8):
    return Assessment(name,group,direction,score,True,["test fixture"])


def test_similar_strategies_do_not_multiply_confirmation():
    one=assessment("a","trend",score=.9)
    result=select_assessments([one,assessment("b","trend",score=.8)],minimum_groups=2)
    assert result.direction==Direction.HOLD
    once=select_assessments([one])
    twice=select_assessments([one,assessment("b","trend",score=.8)])
    assert once.score==twice.score==.9


def test_opposing_qualified_rules_abstain_even_with_majority():
    result=select_assessments([assessment("a","trend"),assessment("b","breakout"),assessment("c","reversion",Direction.SELL)])
    assert result.direction==Direction.HOLD and "Conflicting" in result.reason


def test_highest_scoring_candidate_wins_with_bounded_independent_confirmation():
    result=select_assessments([assessment("b","momentum",score=.8),assessment("a","trend",score=.9)])
    assert result.strategy=="a" and result.direction==Direction.BUY
    assert result.score==pytest.approx(.908)
    assert result.groups==["momentum","trend"]


def test_independent_confirmation_never_lowers_score_or_depends_on_order():
    strongest=assessment("a","trend",score=.9)
    weaker=assessment("b","breakout",score=.7)
    correlated=assessment("c","trend",score=.8)
    first=select_assessments([strongest,weaker,correlated])
    reverse=select_assessments([correlated,weaker,strongest])
    assert first.score==reverse.score>=select_assessments([strongest]).score
    assert first.groups==["breakout","trend"]
    assert select_assessments([strongest,correlated]).score==strongest.score


def test_experimental_conflict_requires_clear_margin_and_is_symmetric():
    buy=assessment("buy","trend",Direction.BUY,.91)
    sell=assessment("sell","breakout",Direction.SELL,.68)
    assert select_assessments([buy,sell]).direction==Direction.HOLD
    assert select_assessments([sell,buy],conflict_policy="quality_margin",conflict_quality_margin=.20).direction==Direction.BUY
    assert select_assessments([buy,sell],conflict_policy="quality_margin",conflict_quality_margin=.25).direction==Direction.HOLD
    reverse=[assessment("buy","trend",Direction.SELL,.91),assessment("sell","breakout",Direction.BUY,.68)]
    assert select_assessments(reverse,conflict_policy="quality_margin",conflict_quality_margin=.20).direction==Direction.SELL


def test_next_executable_strategy_owns_trade_plan_and_metadata(monkeypatch):
    from signals.selector import Selection
    from signals.trade_plan import TradePlan
    cfg=load_config().model_copy(deep=True)
    cfg.settings["trading"]["min_confidence"]=.01
    entries=[assessment("trend_pullback","trend",score=.90),assessment("momentum","momentum",score=.82)]
    monkeypatch.setattr("signals.signal_engine.select_strategy",lambda *a:Selection(Direction.BUY,"trend_pullback",.90,"fixture",["trend"],entries))
    def plan(frames,direction,regime,strategy,cfg,context):
        if strategy=="momentum":
            return TradePlan(True,"BUY",entry=100.5,sl=99.5,tp=102.5,net_rr=2,reason="Executable")
        return TradePlan(False,"BUY",reason="No target meets minimum net reward/risk")
    monkeypatch.setattr("signals.signal_engine.build_trade_plan",plan)
    result=SignalEngine(cfg).generate("EURUSD_o",frames_for("trend_pullback"),MarketRegime.TRENDING_BULLISH,
        {"direction":"BULLISH","score":.8,"reasons":[]},{"score":.7,"reasons":[]},True,"no event",.5,20)
    assert result.direction==Direction.BUY and result.strategy=="momentum"
    assert (result.entry_price,result.stop_loss,result.take_profit)==(100.5,99.5,102.5)
    assert result.meta["selection"]["strategy"]=="momentum"
    assert result.meta["candidate_plans"][0]["accepted"] is False


def test_profile_signal_ids_and_metadata_are_distinct_on_same_candle():
    cfg=load_config().model_copy(deep=True)
    frames=frames_for("trend_pullback")
    arguments=("EURUSD_o",frames,MarketRegime.TRENDING_BULLISH,
        {"direction":"BULLISH","score":.8,"reasons":[]},{"score":.7,"reasons":[]},True,"no event",.5,20)
    scalp=SignalEngine(cfg.for_profile("scalping")).generate(*arguments)
    intra=SignalEngine(cfg.for_profile("intraday")).generate(*arguments)
    assert scalp.signal_id!=intra.signal_id
    assert (scalp.meta["profile"],scalp.timeframe)==("scalping","M1")
    assert (intra.meta["profile"],intra.timeframe)==("intraday","M5")


def test_balanced_reuses_existing_h1_context_rule():
    frames=frames_for("trend_pullback")
    one=strategy_direction(frames,MarketRegime.TRENDING_BULLISH,"trend_pullback",options={"experiment":"h1_context"})
    other=strategy_direction(frames,MarketRegime.TRENDING_BULLISH,"trend_pullback",options={"experiment":"balanced"})
    assert one==other


def test_selector_evaluates_every_enabled_strategy_and_disabled_reasons():
    cfg=load_config().model_copy(deep=True)
    cfg.settings["strategy"]["enabled_strategies"]=["trend_pullback"]
    result=select_strategy(frames_for("trend_pullback"),MarketRegime.TRENDING_BULLISH,cfg,{"direction":"BULLISH"})
    assert len(result.assessments)==len(FAMILIES)
    assert all(a.reasons==["Disabled by configuration"] for a in result.assessments if a.strategy!="trend_pullback")


@pytest.mark.parametrize("regime",[MarketRegime.UNCERTAIN,MarketRegime.HIGH_VOLATILITY,MarketRegime.LOW_VOLATILITY,MarketRegime.NEWS_EVENT])
def test_dangerous_or_unknown_regime_never_selects_a_rule(regime):
    result=select_strategy(frames_for("trend_pullback"),regime,load_config(),{"direction":"RANGE"})
    assert result.direction==Direction.HOLD and all(not a.eligible for a in result.assessments)


def test_missing_indicator_rejects_only_the_affected_rule():
    frames=frames_for("supertrend")
    frames["entry"].loc[31,"supertrend_direction"]=float("nan")
    result=select_strategy(frames,MarketRegime.TRENDING_BULLISH,load_config(),{"direction":"BULLISH"})
    rule=next(a for a in result.assessments if a.strategy=="supertrend")
    assert not rule.eligible and "warming" in rule.reasons[0]
    json.dumps(indicator_snapshot(frames),allow_nan=False)


def test_all_new_indicators_are_prefix_invariant():
    n=300;t=np.arange(n,dtype=float)
    close=100+.04*t+np.sin(t/6)
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"),"open":close-.05,"high":close+.3,"low":close-.3,"close":close,"tick_volume":100+t%10})
    cfg=load_config().get("strategy.indicators")
    prefix=add_indicators(bars.iloc[:250],cfg)
    changed=bars.copy()
    for col in ("open","high","low","close"): changed.loc[250:,col]*=2
    full=add_indicators(changed,cfg)
    assert_frame_equal(prefix,full.iloc[:250])
    columns=["stoch_k","stoch_d","cci","williams_r","roc","vwap","cloud_a","cloud_b","supertrend","supertrend_direction","donchian_high","keltner_high"]
    assert np.isfinite(prefix[columns].iloc[-1]).all()


def test_ichimoku_cloud_uses_past_bars_not_future_prices():
    t=np.arange(120,dtype=float);close=100+t
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=120,freq="5min",tz="UTC"),"open":close,"high":close+1,"low":close-1,"close":close,"tick_volume":100})
    df=add_indicators(bars,{})
    assert df.cloud_a.iloc[100]==pytest.approx((df.tenkan.iloc[74]+df.kijun.iloc[74])/2)
    assert df.donchian_high.iloc[100]==pytest.approx(bars.high.iloc[80:100].max())


def test_news_veto_clears_order_levels_but_keeps_all_assessments():
    cfg=load_config().model_copy(deep=True)
    cfg.settings["trading"]["min_confidence"]=.01
    signal=SignalEngine(cfg).generate("EURUSD_o",frames_for("trend_pullback"),MarketRegime.TRENDING_BULLISH,
        {"direction":"BULLISH","score":.8,"reasons":[]},{"score":.7,"reasons":[]},False,"Calendar unavailable",0,20)
    assert signal.direction==Direction.HOLD and signal.stop_loss is None and signal.take_profit is None
    assert signal.meta["selection"]["direction"]=="BUY"
    assert len(signal.meta["selection"]["assessments"])==len(FAMILIES)


def test_breakout_regime_is_recognized_without_a_future_bar():
    frames=frames_for("breakout")
    frames["entry"]["bb_width"]=2.
    assert detect_regime(frames["entry"])==MarketRegime.BREAKOUT


def test_volume_quality_is_symmetric_for_buy_and_sell():
    from signals.confidence_engine import ConfidenceEngine
    engine=ConfidenceEngine({"volume":1})
    assert engine.score({"volume":.8},"BUY")==engine.score({"volume":.8},"SELL")==.8


def test_changing_winning_rule_on_same_bar_does_not_create_new_ensemble_signal_id(monkeypatch):
    from signals.selector import Selection
    cfg=load_config().model_copy(deep=True);cfg.settings["trading"]["min_confidence"]=.01
    engine=SignalEngine(cfg);frames=frames_for("trend_pullback")
    def generate():
        return engine.generate("EURUSD_o",frames,MarketRegime.TRENDING_BULLISH,
            {"direction":"BULLISH","score":.8,"reasons":[]},{"score":.7,"reasons":[]},True,"no event",.5,20)
    monkeypatch.setattr("signals.signal_engine.select_strategy",lambda *a:Selection(Direction.BUY,"trend_pullback",.8,"fixture",["trend"],[assessment("trend_pullback","trend")]))
    first=generate()
    monkeypatch.setattr("signals.signal_engine.select_strategy",lambda *a:Selection(Direction.BUY,"momentum",.8,"fixture",["momentum"],[assessment("momentum","momentum")]))
    second=generate()
    assert first.direction==second.direction==Direction.BUY
    assert first.strategy!=second.strategy and first.signal_id==second.signal_id


def test_strategy_details_survive_database_round_trip(tmp_path):
    from database.repository import Repository
    from database.models import SignalRecord
    from sqlalchemy.orm import Session
    cfg=load_config()
    signal=SignalEngine(cfg).generate("EURUSD_o",frames_for("trend_pullback"),MarketRegime.TRENDING_BULLISH,
        {"direction":"BULLISH","score":.8,"reasons":[]},{"score":.7,"reasons":[]},False,"news blocked",0,20)
    repo=Repository("sqlite:///"+str(tmp_path/"signals.db"))
    assert repo.save_signal(signal)
    with Session(repo.engine) as session:
        row=session.query(SignalRecord).one()
        payload=json.loads(row.payload)
    assert len(payload["analysis"]["selection"]["assessments"])==len(FAMILIES)
    assert payload["strategy"]==signal.strategy
    assert "oscillators" in payload["analysis"]["indicators"]["entry"]


def test_scanner_is_read_only_and_explicit_about_historical_news(broker,monkeypatch):
    from analysis.scanner import scan_symbol
    from core.data_feed import DataFeed
    # Build real indicator inputs with enough warmup for the scanner.
    close=100+np.arange(240)*.01+np.sin(np.arange(240)/5)
    data=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=240,freq="5min",tz="UTC"),"open":close-.05,"high":close+.2,"low":close-.2,"close":close,"tick_volume":100})
    requests=[]
    def bars(*a,**kw):
        requests.append(kw)
        return data.copy()
    def forbidden(*a,**kw): raise AssertionError("Scanner must not construct execution or send orders")
    monkeypatch.setattr(DataFeed,"bars",bars)
    monkeypatch.setattr(broker,"order_send",forbidden)
    monkeypatch.setattr("core.execution.ExecutionEngine.__init__",forbidden)
    report=scan_symbol(load_config(),broker,"EURUSD")
    assert len(requests)==4 and all(r["require_fresh"] is False for r in requests)
    assert report["read_only"] and not report["orders_enabled"] and report["final_direction"]=="HOLD"
    assert report["live_chart"]["source"]=="MT5"
    assert len(report["selection"]["assessments"])==len(FAMILIES)
    assert any("news not validated" in reason for reason in report["reasons"])


def test_configuration_rejects_empty_or_duplicate_rules():
    cfg=load_config().model_copy(deep=True)
    cfg.settings["strategy"]["enabled_strategies"]=["momentum","momentum"]
    with pytest.raises(ValueError,match="unique"):
        cfg.validate_runtime()
