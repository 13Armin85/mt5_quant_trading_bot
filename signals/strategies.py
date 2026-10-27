from __future__ import annotations

import math
from utils.types import Direction, MarketRegime

FAMILIES = ("trend_pullback", "breakout", "mean_reversion", "momentum", "ema_cross", "macd_cross",
            "ichimoku", "supertrend", "stochastic_reversal", "cci_reversion", "keltner_breakout", "vwap_reclaim")

GROUPS = {"trend_pullback":"trend", "ema_cross":"trend", "ichimoku":"trend", "supertrend":"trend", "vwap_reclaim":"trend",
          "breakout":"breakout", "keltner_breakout":"breakout", "momentum":"momentum", "macd_cross":"momentum",
          "mean_reversion":"reversion", "stochastic_reversal":"reversion", "cci_reversion":"reversion"}

EXTRA_COLUMNS = {
    "ema_cross": ("ema_50",), "macd_cross": ("macd", "macd_signal"),
    "ichimoku": ("tenkan", "kijun", "cloud_a", "cloud_b"), "supertrend": ("supertrend_direction",),
    "stochastic_reversal": ("stoch_k", "stoch_d"), "cci_reversion": ("cci",),
    "keltner_breakout": ("keltner_high", "keltner_low", "relative_volume"), "vwap_reclaim": ("vwap", "obv"),
    "breakout": ("relative_volume",), "mean_reversion": ("bb_low", "bb_high"),
}


def breakout_retest(frame, lookback, expiry=6, tolerance_atr=.2):
    """Replay a bounded closed-bar setup; first retest only, with expiry/invalidation."""
    last = len(frame)-1
    for origin in range(last-1,max(lookback-1,last-expiry-1),-1):
        trigger = frame.iloc[origin]
        prior = frame.iloc[origin-lookback:origin]
        side = 1 if trigger.close>prior.high.max() else -1 if trigger.close<prior.low.min() else 0
        if not side:
            continue
        level = float(prior.high.max() if side==1 else prior.low.min())
        buffer = float(trigger.atr)*tolerance_atr
        for index in range(origin+1,last+1):
            row = frame.iloc[index]
            if side*(row.close-level)<-buffer:
                break
            touched = row.low<=level+buffer if side==1 else row.high>=level-buffer
            confirmed = side*(row.close-level)>0 and side*(row.close-row.open)>0
            if touched and confirmed:
                if index==last:
                    return Direction.BUY if side==1 else Direction.SELL, f"Retest confirmed; origin={trigger.time}; level={level}"
                return Direction.HOLD,"Breakout retest already consumed on a prior closed bar"
        # The latest breakout defines the current state; older levels are superseded.
        return Direction.HOLD,"Retest waiting, expired or invalidated"
    return Direction.HOLD,"No unexpired breakout to retest"


def strategy_direction(frames, regime, family="auto", lookback=20, pullback_atr=.5, options=None):
    """Closed-bar rules. Stops/targets are ATR based in SignalEngine.

    Auto selects a regime rule, never claims an empirically optimal strategy.
    """
    if family not in (*FAMILIES, "auto") or lookback < 2 or pullback_atr <= 0:
        raise ValueError("Invalid strategy parameters")
    if regime in {MarketRegime.HIGH_VOLATILITY, MarketRegime.LOW_VOLATILITY, MarketRegime.UNCERTAIN, MarketRegime.NEWS_EVENT}:
        return Direction.HOLD, family, "Market regime blocks entry"
    if any(role not in frames or len(frames[role]) < 2 for role in ("entry", "macro", "trend", "setup")):
        return Direction.HOLD, family, "Insufficient multi-timeframe history"
    x, previous = frames["entry"].iloc[-1], frames["entry"].iloc[-2]
    macro, trend, setup = (frames[k].iloc[-1] for k in ("macro", "trend", "setup"))
    if not all(math.isfinite(float(row.get(column,float("nan")))) for row in (macro,trend) for column in ("ema_20","ema_50","ema_200")):
        return Direction.HOLD, family, "Higher-timeframe indicator warmup incomplete"
    if not all(math.isfinite(float(x.get(c,float("nan")))) for c in ("atr","rsi","ema_20")) or x.atr <= 0:
        return Direction.HOLD, family, "Indicator warmup incomplete"
    if family == "auto":
        family = "mean_reversion" if regime == MarketRegime.RANGING else "breakout" if regime == MarketRegime.BREAKOUT else "trend_pullback"
    columns = ("close", "open", "high", "low", "ema_20", "atr", "rsi", *EXTRA_COLUMNS.get(family, ()))
    if any(not math.isfinite(float(row.get(c, float("nan")))) for row in (x, previous) for c in columns):
        return Direction.HOLD, family, "Required indicators unavailable or still warming up"
    if GROUPS[family] == "reversion" and regime != MarketRegime.RANGING:
        return Direction.HOLD, family, "Reversion requires a ranging market"
    if GROUPS[family] == "trend" and regime == MarketRegime.RANGING:
        return Direction.HOLD, family, "Trend entry requires a trending/breakout market"
    bullish = macro.ema_20 > macro.ema_50 > macro.ema_200 and trend.ema_20 > trend.ema_50 > trend.ema_200
    bearish = macro.ema_20 < macro.ema_50 < macro.ema_200 and trend.ema_20 < trend.ema_50 < trend.ema_200
    options = options or {}
    experiment = options.get("experiment","baseline")
    if experiment=="h1_context" and family=="trend_pullback":
        bullish = trend.ema_20 > trend.ema_50 > trend.ema_200 and setup.ema_20>setup.ema_50
        bearish = trend.ema_20 < trend.ema_50 < trend.ema_200 and setup.ema_20<setup.ema_50
    buy = sell = False
    if family == "trend_pullback":
        buy = bullish and previous.low <= previous.ema_20 + previous.atr * pullback_atr and x.close > x.ema_20 and x.close > previous.close
        sell = bearish and previous.high >= previous.ema_20 - previous.atr * pullback_atr and x.close < x.ema_20 and x.close < previous.close
    elif family == "breakout":
        if len(frames["entry"])<lookback+1:
            return Direction.HOLD, family, "Insufficient breakout lookback"
        prior = frames["entry"].iloc[-lookback-1:-1]
        healthy = float(options.get("min_atr_fraction",.0005)) <= x.atr / x.close <= float(options.get("max_atr_fraction",.02)) and x.relative_volume >= float(options.get("min_relative_volume",.85))
        if experiment=="breakout_retest":
            direction, reason = breakout_retest(frames["entry"],lookback,int(options.get("retest_expiry_bars",6)),float(options.get("retest_tolerance_atr",.2)))
            if not healthy or (direction==Direction.BUY and bearish) or (direction==Direction.SELL and bullish):
                return Direction.HOLD,family,"Retest cost/volatility/direction filter blocked entry"
            return direction,family,reason
        buy = healthy and not bearish and x.close > prior.high.max() and x.close > x.open
        sell = healthy and not bullish and x.close < prior.low.min() and x.close < x.open
    elif family == "mean_reversion":
        buy = regime == MarketRegime.RANGING and previous.close < previous.bb_low and previous.rsi < 35 and x.close > x.bb_low and x.close > previous.close
        sell = regime == MarketRegime.RANGING and previous.close > previous.bb_high and previous.rsi > 65 and x.close < x.bb_high and x.close < previous.close
    elif family == "momentum":
        buy = bullish and 50 < setup.rsi < 70 and setup.macd_hist > 0 and x.close > previous.high
        sell = bearish and 30 < setup.rsi < 50 and setup.macd_hist < 0 and x.close < previous.low
    elif family == "ema_cross":
        buy = bullish and previous.ema_20 <= previous.ema_50 and x.ema_20 > x.ema_50 and x.close > x.ema_20
        sell = bearish and previous.ema_20 >= previous.ema_50 and x.ema_20 < x.ema_50 and x.close < x.ema_20
    elif family == "macd_cross":
        buy = bullish and previous.macd <= previous.macd_signal and x.macd > x.macd_signal and x.close > x.ema_20
        sell = bearish and previous.macd >= previous.macd_signal and x.macd < x.macd_signal and x.close < x.ema_20
    elif family == "ichimoku":
        roof, floor = max(x.cloud_a, x.cloud_b), min(x.cloud_a, x.cloud_b)
        buy = bullish and x.tenkan > x.kijun and x.close > roof and previous.close <= max(previous.cloud_a, previous.cloud_b)
        sell = bearish and x.tenkan < x.kijun and x.close < floor and previous.close >= min(previous.cloud_a, previous.cloud_b)
    elif family == "supertrend":
        buy = bullish and previous.supertrend_direction < 0 and x.supertrend_direction > 0
        sell = bearish and previous.supertrend_direction > 0 and x.supertrend_direction < 0
    elif family == "stochastic_reversal":
        buy = previous.stoch_k <= previous.stoch_d and x.stoch_k > x.stoch_d and min(previous.stoch_k, previous.stoch_d) < 20 and x.close > previous.close
        sell = previous.stoch_k >= previous.stoch_d and x.stoch_k < x.stoch_d and max(previous.stoch_k, previous.stoch_d) > 80 and x.close < previous.close
    elif family == "cci_reversion":
        buy = previous.cci < -100 and x.cci >= -100 and x.close > previous.close
        sell = previous.cci > 100 and x.cci <= 100 and x.close < previous.close
    elif family == "keltner_breakout":
        healthy = float(options.get("min_atr_fraction",.0005)) <= x.atr / x.close <= float(options.get("max_atr_fraction",.02)) and x.relative_volume >= float(options.get("min_relative_volume",.85))
        buy = healthy and not bearish and previous.close <= previous.keltner_high and x.close > x.keltner_high and x.close > x.open
        sell = healthy and not bullish and previous.close >= previous.keltner_low and x.close < x.keltner_low and x.close < x.open
    elif family == "vwap_reclaim":
        buy = bullish and previous.close <= previous.vwap and x.close > x.vwap and x.obv > previous.obv
        sell = bearish and previous.close >= previous.vwap and x.close < x.vwap and x.obv < previous.obv
    if buy and sell:
        return Direction.HOLD, family, "Conflicting strategy directions"
    direction = Direction.BUY if buy else Direction.SELL if sell else Direction.HOLD
    return direction, family, f"{family}: {'closed-bar setup confirmed' if buy or sell else 'entry conditions absent'}"
