from __future__ import annotations

from utils.types import MarketRegime
import math


def detect_regime(df, filters: dict | None = None) -> MarketRegime:
    filters = filters or {}
    x = df.iloc[-1]
    if not all(math.isfinite(float(x.get(k,float("nan")))) for k in ("close","atr","adx","bb_width","ema_20","ema_50","ema_200")) or x.close <= 0 or x.atr <= 0:
        return MarketRegime.UNCERTAIN
    adx_min = float(filters.get("min_adx_trending", 22))
    atr_pct = float(x.atr / x.close) if x.close else 0.0
    if atr_pct > float(filters.get("high_volatility_fraction",.04)):
        return MarketRegime.HIGH_VOLATILITY
    lookback = int(filters.get("breakout_lookback",20))
    if len(df)>lookback and float(filters.get("min_atr_fraction",.0005))<=atr_pct<=float(filters.get("max_atr_fraction",.02)):
        prior = df.iloc[-lookback-1:-1]
        relative_volume = float(x.get("relative_volume",0))
        if relative_volume >= float(filters.get("min_relative_volume",.85)) and (x.close > prior.high.max() or x.close < prior.low.min()):
            return MarketRegime.BREAKOUT
    if x.bb_width < df.bb_width.iloc[:-1].tail(100).quantile(float(filters.get("compression_quantile",.2))):
        return MarketRegime.LOW_VOLATILITY
    if x.adx >= adx_min and x.ema_20 > x.ema_50 > x.ema_200:
        return MarketRegime.TRENDING_BULLISH
    if x.adx >= adx_min and x.ema_20 < x.ema_50 < x.ema_200:
        return MarketRegime.TRENDING_BEARISH
    if x.adx < adx_min:
        return MarketRegime.RANGING
    return MarketRegime.UNCERTAIN
