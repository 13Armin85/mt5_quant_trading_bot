from __future__ import annotations

import math
from utils.types import Direction, MarketRegime

FAMILIES = ("trend_pullback", "breakout", "mean_reversion", "momentum")


def strategy_direction(frames, regime, family="auto", lookback=20, pullback_atr=.5):
    """Closed-bar rules. Stops/targets are ATR based in SignalEngine.

    Auto selects a regime rule, never claims an empirically optimal strategy.
    """
    if family not in (*FAMILIES, "auto") or lookback < 2 or pullback_atr <= 0:
        raise ValueError("Invalid strategy parameters")
    if regime in {MarketRegime.HIGH_VOLATILITY, MarketRegime.LOW_VOLATILITY, MarketRegime.UNCERTAIN, MarketRegime.NEWS_EVENT}:
        return Direction.HOLD, family, "Market regime blocks entry"
    x, previous = frames["entry"].iloc[-1], frames["entry"].iloc[-2]
    macro, trend, setup = (frames[k].iloc[-1] for k in ("macro", "trend", "setup"))
    if not all(math.isfinite(float(v)) for v in (x.atr, x.rsi, x.ema_20, macro.ema_200, trend.ema_200)) or x.atr <= 0:
        return Direction.HOLD, family, "Indicator warmup incomplete"
    if family == "auto":
        family = "mean_reversion" if regime == MarketRegime.RANGING else "breakout" if regime == MarketRegime.BREAKOUT else "trend_pullback"
    bullish = macro.ema_20 > macro.ema_50 > macro.ema_200 and trend.ema_20 > trend.ema_50 > trend.ema_200
    bearish = macro.ema_20 < macro.ema_50 < macro.ema_200 and trend.ema_20 < trend.ema_50 < trend.ema_200
    buy = sell = False
    if family == "trend_pullback":
        buy = bullish and previous.low <= previous.ema_20 + previous.atr * pullback_atr and x.close > x.ema_20 and x.close > previous.close
        sell = bearish and previous.high >= previous.ema_20 - previous.atr * pullback_atr and x.close < x.ema_20 and x.close < previous.close
    elif family == "breakout":
        prior = frames["entry"].iloc[-lookback-1:-1]
        healthy = .0005 <= x.atr / x.close <= .02 and x.relative_volume >= .85
        buy = healthy and not bearish and x.close > prior.high.max() and x.close > x.open
        sell = healthy and not bullish and x.close < prior.low.min() and x.close < x.open
    elif family == "mean_reversion":
        buy = regime == MarketRegime.RANGING and previous.close < previous.bb_low and previous.rsi < 35 and x.close > x.bb_low and x.close > previous.close
        sell = regime == MarketRegime.RANGING and previous.close > previous.bb_high and previous.rsi > 65 and x.close < x.bb_high and x.close < previous.close
    elif family == "momentum":
        buy = bullish and 50 < setup.rsi < 70 and setup.macd_hist > 0 and x.close > previous.high
        sell = bearish and 30 < setup.rsi < 50 and setup.macd_hist < 0 and x.close < previous.low
    if buy and sell:
        return Direction.HOLD, family, "Conflicting strategy directions"
    direction = Direction.BUY if buy else Direction.SELL if sell else Direction.HOLD
    return direction, family, f"{family}: {'closed-bar setup confirmed' if buy or sell else 'entry conditions absent'}"
