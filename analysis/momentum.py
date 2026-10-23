from __future__ import annotations


def score_momentum(df) -> tuple[float, list[str]]:
    x = df.iloc[-1]; score = 0.5; reasons = []
    if 50 <= x.rsi <= 70: score += 0.18; reasons.append(f"RSI bullish {x.rsi:.1f}")
    elif 30 <= x.rsi < 50: score -= 0.18; reasons.append(f"RSI bearish {x.rsi:.1f}")
    elif x.rsi > 75: score -= 0.08; reasons.append("RSI overbought")
    elif x.rsi < 25: score += 0.08; reasons.append("RSI oversold")
    if x.macd_hist > 0: score += 0.14; reasons.append("MACD histogram positive")
    else: score -= 0.14; reasons.append("MACD histogram negative")
    return max(0.0, min(1.0, score)), reasons
