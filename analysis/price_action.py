from __future__ import annotations


def analyze_price_action(df) -> dict:
    if len(df) < 3:
        return {"score": 0.5, "reasons": []}
    a, b = df.iloc[-2], df.iloc[-1]
    body = abs(b.close - b.open); rng = max(b.high - b.low, 1e-12)
    upper = b.high - max(b.open, b.close); lower = min(b.open, b.close) - b.low
    bullish = b.close > b.open; score = 0.5; reasons = []
    if body / rng < 0.1: reasons.append("Doji")
    if lower > body * 2 and upper < body: score += 0.15; reasons.append("Bullish rejection / pin bar")
    if upper > body * 2 and lower < body: score -= 0.15; reasons.append("Bearish rejection / pin bar")
    if b.high > a.high and b.low < a.low: reasons.append("Outside bar")
    if b.high < a.high and b.low > a.low: reasons.append("Inside bar")
    if bullish and a.close < a.open and b.close >= a.open and b.open <= a.close:
        score += 0.2; reasons.append("Bullish engulfing")
    if not bullish and a.close > a.open and b.open >= a.close and b.close <= a.open:
        score -= 0.2; reasons.append("Bearish engulfing")
    if body / rng > 0.7:
        score += 0.1 if bullish else -0.1; reasons.append("Momentum candle")
    return {"score": max(0.0, min(1.0, score)), "reasons": reasons}
