from __future__ import annotations


def score_trend(df) -> tuple[float, list[str]]:
    x = df.iloc[-1]
    score = 0.5; reasons = []
    if x.ema_20 > x.ema_50 > x.ema_200:
        score = 0.9; reasons.append("EMA20 > EMA50 > EMA200")
    elif x.ema_20 < x.ema_50 < x.ema_200:
        score = 0.1; reasons.append("EMA20 < EMA50 < EMA200")
    elif x.close > x.ema_200:
        score = 0.65; reasons.append("Price above EMA200")
    elif x.close < x.ema_200:
        score = 0.35; reasons.append("Price below EMA200")
    if x.adx >= 25:
        reasons.append(f"ADX={x.adx:.1f}")
        if x.adx_pos > x.adx_neg: score = min(1.0, score + 0.05)
        if x.adx_neg > x.adx_pos: score = max(0.0, score - 0.05)
    return score, reasons
