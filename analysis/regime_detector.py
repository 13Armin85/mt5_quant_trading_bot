from __future__ import annotations

from utils.types import MarketRegime


def detect_regime(df, filters: dict | None = None) -> MarketRegime:
    filters = filters or {}
    x = df.iloc[-1]
    adx_min = float(filters.get("min_adx_trending", 22))
    atr_pct = float(x.atr / x.close) if x.close else 0.0
    if atr_pct > 0.04:
        return MarketRegime.HIGH_VOLATILITY
    if x.bb_width < df.bb_width.tail(100).quantile(0.2):
        return MarketRegime.LOW_VOLATILITY
    if x.adx >= adx_min and x.ema_20 > x.ema_50 > x.ema_200:
        return MarketRegime.TRENDING_BULLISH
    if x.adx >= adx_min and x.ema_20 < x.ema_50 < x.ema_200:
        return MarketRegime.TRENDING_BEARISH
    if x.adx < adx_min:
        return MarketRegime.RANGING
    return MarketRegime.UNCERTAIN
