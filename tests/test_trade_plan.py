import numpy as np
import pandas as pd
import pytest

from config import load_config
from signals.trade_plan import build_trade_plan,net_reward_risk
from utils.types import Direction,MarketRegime,OrderRequest
from analysis.indicators import add_indicators
from backtest.engine import BacktestEngine


def config(structure=False):
    cfg=load_config().model_copy(deep=True)
    cfg.settings["strategy"]["reward_risk"]["respect_structure"]=structure
    return cfg


def frames():
    df=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=40,freq="5min",tz="UTC"),
                     "open":100.,"high":100.2,"low":99.8,"close":100.,"atr":1.,"bb_mid":101.})
    return {role:df.copy() for role in ("entry","setup","trend","macro")}


def context(commission=7,slip=.1,tick=.01):
    return {"profit":lambda side,start,end:(1 if side=="BUY" else -1)*(end-start)*100,
            "commission_per_lot":commission,"exit_slippage":slip,"tick_size":tick,
            "cost_basis":"broker_account_currency_estimate"}


@pytest.mark.parametrize("direction",[Direction.BUY,Direction.SELL])
@pytest.mark.parametrize("regime,wanted",[(MarketRegime.RANGING,1.5),(MarketRegime.TRENDING_BULLISH,2),(MarketRegime.BREAKOUT,2.5)])
def test_regime_targets_are_net_of_commission_and_slippage(direction,regime,wanted):
    plan=build_trade_plan(frames(),direction,regime,"momentum",config(),context())
    assert plan.accepted and plan.target_net_rr==wanted
    assert plan.net_rr+1e-8>=wanted and plan.gross_rr>plan.net_rr
    assert plan.break_even_win_rate==pytest.approx(1/(1+plan.net_rr))
    assert plan.loss_per_lot>0 and plan.reward_per_lot>0
    assert abs(plan.sl/.01-round(plan.sl/.01))<1e-8
    assert abs(plan.tp/.01-round(plan.tp/.01))<1e-8


def test_spread_and_entry_slippage_are_in_the_planned_entry():
    cx=context();cx["spread_price"]=.2
    buy=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",config(),cx)
    sell=build_trade_plan(frames(),Direction.SELL,MarketRegime.TRENDING_BEARISH,"momentum",config(),cx)
    assert buy.entry==pytest.approx(100.3) and sell.entry==pytest.approx(99.9)


def test_profile_rejects_round_trip_cost_that_consumes_too_much_stop_budget():
    cfg=config(); cfg.settings["profile"]={"name":"scalping","max_cost_to_stop_fraction":.20}
    cx=context(); cx["spread_price"]=.8
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",cfg,cx)
    assert not plan.accepted and plan.cost_to_stop_fraction>.20
    assert "cost-to-stop" in plan.reason


def test_near_resistance_can_reduce_target_without_crossing_it(monkeypatch):
    monkeypatch.setattr("signals.trade_plan._swings",lambda *a:([(20,103.2)],[(20,99.)]))
    cfg=config(True);cfg.settings["strategy"]["reward_risk"]["trend_target"]=2.5
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",cfg,context(0,0))
    assert plan.accepted and plan.target_net_rr==2
    assert plan.tp<=plan.obstacle-.1+1e-9
    assert not plan.candidates[0]["accepted"]


@pytest.mark.parametrize("direction,level",[(Direction.BUY,101.),(Direction.SELL,99.)])
def test_no_room_for_minimum_reward_rejects_entry(monkeypatch,direction,level):
    monkeypatch.setattr("signals.trade_plan._swings",lambda *a:([(20,level)],[(20,level)]))
    plan=build_trade_plan(frames(),direction,MarketRegime.TRENDING_BULLISH,"momentum",config(True),context(0,0))
    assert not plan.accepted and plan.tp is None and "No target" in plan.reason


def test_structural_invalidation_is_not_tightened_to_force_a_small_stop(monkeypatch):
    monkeypatch.setattr("signals.trade_plan._swings",lambda *a:([(20,110.)],[(20,95.)]))
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",config(True),context(0,0))
    assert not plan.accepted and "ATR distance" in plan.reason
    assert plan.sl<95 and plan.stop_source.startswith("Confirmed")


def test_range_target_respects_the_bollinger_mean(monkeypatch):
    monkeypatch.setattr("signals.trade_plan._swings",lambda *a:([],[]))
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.RANGING,"mean_reversion",config(True),context(0,0))
    assert not plan.accepted and plan.obstacle==101


def test_failed_broker_profit_calculation_rejects_plan():
    cx=context();cx["profit"]=lambda *a:None
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",config(),cx)
    assert not plan.accepted and "unavailable" in plan.reason


def test_geometry_only_plan_does_not_claim_account_currency_amounts():
    plan=build_trade_plan(frames(),Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",config())
    assert plan.accepted and plan.cost_basis=="unverified_price_geometry"
    assert plan.loss_per_lot is None and plan.reward_per_lot is None


def test_two_to_one_gross_is_not_two_to_one_net():
    pnl=lambda start,end:(end-start)*100
    outcome=net_reward_risk("BUY",100,98.5,103,pnl,7,.1)
    assert outcome["gross_rr"]==2 and outcome["net_rr"]<2


def test_live_gate_rejects_old_gross_ratio_after_costs(live_engine,broker):
    req=OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.099,1.102,20,signal_id="net-too-low")
    result=live_engine.execute(req)
    assert not result.success and "net reward/risk" in result.message
    assert not broker.sent


def test_live_quote_change_cannot_bypass_net_ratio_recheck(live_engine,broker,monkeypatch):
    def check(req):
        broker.quote.ask=1.1008;broker.quote.bid=1.1006
        return type("Check",(),{"retcode":0})()
    monkeypatch.setattr(broker,"order_check",check)
    result=live_engine.execute(OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.099,1.103,20,signal_id="moved-quote"))
    assert not result.success and not broker.sent


def test_live_entry_slippage_is_reserved_in_the_budget(live_engine,broker):
    req=OrderRequest("EURUSD",Direction.BUY,.3,1.1,1.099,1.103,20,signal_id="reserved-slippage")
    result=live_engine.execute(req)
    assert result.success
    actual=live_engine.risk.loss_per_lot("EURUSD",req.price,req.sl,broker.info,Direction.BUY)*result.filled_volume
    assert result.planned_risk>actual and result.planned_risk<=100


@pytest.mark.parametrize("key,value",[("minimum_net_rr",float("nan")),("max_stop_atr",.5),("structure_buffer_atr",-.1),("candidates",[0,2])])
def test_invalid_rr_configuration_is_rejected(key,value):
    cfg=config();cfg.settings["strategy"]["reward_risk"][key]=value
    with pytest.raises(ValueError):cfg.validate_runtime()


def test_request_can_enforce_stricter_policy_minimum(live_engine,broker):
    req=OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.099,1.103,20,signal_id="strict",minimum_net_rr=3)
    assert not live_engine.execute(req).success and not broker.sent


def test_backtest_rejects_gap_that_destroys_net_reward_ratio():
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=3,freq="h",tz="UTC"),"open":[100,101,101],"high":[100,103,102],"low":[100,100,100],"close":[100,101,101]})
    signals=pd.DataFrame([{"time":bars.time.iloc[0],"direction":"BUY","entry":100,"sl":99,"tp":103,"contract_value_per_price":1,"minimum_net_rr":2}])
    engine=BacktestEngine()
    trades,_=engine.run(bars,signals)
    assert trades.empty and "Net reward/risk" in engine.rejections[0]["reason"]


def test_default_risk_budget_is_half_a_percent():
    from core.risk_manager import RiskManager
    cfg=load_config()
    assert RiskManager(cfg.get("trading")).risk_amount(300)==1.5
    assert cfg.get("trading.max_total_risk")==.01


def test_future_prices_cannot_change_stop_or_obstacle():
    n=280;t=np.arange(n);close=100+.01*t+np.sin(t/5)
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"),"open":close-.05,"high":close+.3,"low":close-.3,"close":close,"tick_volume":100})
    cfg=config(True)
    past=add_indicators(bars.iloc[:250],cfg.get("strategy.indicators"))
    altered=bars.copy()
    for column in ("open","high","low","close"):altered.loc[250:,column]*=2
    full=add_indicators(altered,cfg.get("strategy.indicators")).iloc[:250]
    p=build_trade_plan({r:past for r in ("entry","setup","trend","macro")},Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",cfg,context())
    q=build_trade_plan({r:full for r in ("entry","setup","trend","macro")},Direction.BUY,MarketRegime.TRENDING_BULLISH,"momentum",cfg,context())
    assert p.sl is not None and p.to_dict()==q.to_dict()
