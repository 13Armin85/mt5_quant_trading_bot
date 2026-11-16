from copy import deepcopy

import pytest

from config import load_config
from core.risk_manager import RiskManager
from utils.types import Direction, MarketRegime, Signal


def qualified(side="BUY"):
    trend = {"ema_20":1.12, "ema_50":1.11, "ema_200":1.10,
        "adx":30, "adx_pos":25, "adx_neg":12}
    if side == "SELL":
        trend.update(ema_20=1.10, ema_200=1.12, adx_pos=12, adx_neg=25)
    return Signal("EURUSD", Direction(side), .90, risk_reward=2.3,
        market_regime=MarketRegime.TRENDING_BULLISH if side == "BUY" else MarketRegime.TRENDING_BEARISH,
        meta={"profile":"baseline", "score_type":"uncalibrated_rule_suitability",
            "selection":{"score_type":"uncalibrated_rule_suitability", "direction":side,
                "score":.86, "confirming_groups":["trend","momentum"]},
            "trade_plan":{"accepted":True, "net_rr":2.3},
            "indicators":{role:{"trend":deepcopy(trend)} for role in ("macro","trend")}})


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_cautious_normal_strong_lots_follow_loss_budget_in_both_directions(broker, side):
    risk = RiskManager(load_config().get("trading"), broker)
    signal = qualified(side)
    entry, stop = 1.1, 1.099 if side == "BUY" else 1.101
    volumes = []
    for confidence, expected, tier in ((.75,.0025,"CAUTIOUS"),(.80,.005,"BASE"),(.90,.009,"STRONG_SETUP")):
        signal.confidence = confidence
        fraction, actual_tier = risk.signal_risk_fraction(signal)
        assert (fraction, actual_tier) == (expected, tier)
        volume = risk.calculate_volume("EURUSD", entry, stop, 10000, broker.info,
            direction=side, risk_fraction=fraction)
        loss = risk.loss_per_lot("EURUSD", entry, stop, broker.info, side) * volume
        assert 0 < loss <= 10000 * expected
        volumes.append(volume)
    assert volumes[0] < volumes[1] < volumes[2]
    assert risk.calculate_volume("EURUSD", entry, stop, 10000, broker.info,
        direction=side, open_risk=90, risk_fraction=.009) < volumes[0]


@pytest.mark.parametrize("failure", ["opposite_regime", "missing_macro", "nan", "weak_adx",
    "opposite_di", "opposite_ema", "low_suitability", "losing_streak"])
def test_score_alone_never_increases_lots(failure):
    risk = RiskManager(load_config().get("trading"))
    signal = qualified()
    if failure == "opposite_regime": signal.market_regime = MarketRegime.TRENDING_BEARISH
    if failure == "missing_macro": del signal.meta["indicators"]["macro"]
    if failure == "nan": signal.meta["indicators"]["macro"]["trend"]["ema_200"] = float("nan")
    if failure == "weak_adx": signal.meta["indicators"]["trend"]["trend"]["adx"] = 15
    if failure == "opposite_di": signal.meta["indicators"]["macro"]["trend"]["adx_neg"] = 40
    if failure == "opposite_ema": signal.meta["indicators"]["macro"]["trend"]["ema_20"] = 1.08
    if failure == "low_suitability": signal.meta["selection"]["score"] = .70
    if failure == "losing_streak":
        risk.initialize_state(10000)
        risk.state.consecutive_losses = 1
    assert risk.signal_risk_fraction(signal) == (.0025,"CAUTIOUS")


def test_related_votes_and_low_reward_do_not_qualify_for_larger_size():
    risk = RiskManager(load_config().get("trading"))
    signal = qualified()
    signal.meta["selection"]["confirming_groups"] = ["trend", "trend"]
    assert risk.signal_risk_fraction(signal) == (.005,"BASE")
    signal.meta["selection"]["confirming_groups"] = ["trend","momentum"]
    signal.meta["trade_plan"]["net_rr"] = 1.9
    assert risk.signal_risk_fraction(signal) == (.005,"BASE")


@pytest.mark.parametrize("change", [
    {"cautious_risk_per_trade":0}, {"cautious_risk_per_trade":.006},
    {"cautious_risk_per_trade":float("nan")}, {"normal_min_confidence":.90},
    {"normal_min_confidence":.65}, {"normal_min_suitability":.81}, {"min_trend_adx":10},
])
def test_invalid_adaptive_risk_policy_is_rejected(change):
    cfg = load_config().model_copy(deep=True)
    cfg.settings["trading"]["quality_sizing"].update(change)
    with pytest.raises(ValueError, match="adaptive sizing"):
        cfg.validate_runtime()
