from __future__ import annotations


def simple_sentiment(headlines: list[str]) -> tuple[str, float]:
    if not headlines:
        return "NEUTRAL", 0.0
    pos = {"beats", "growth", "surge", "strong", "hawkish", "upgrade"}
    neg = {"misses", "drop", "weak", "recession", "dovish", "downgrade"}
    score = sum(sum(w in h.lower() for w in pos) - sum(w in h.lower() for w in neg) for h in headlines) / max(len(headlines), 1)
    if score > 0.75: return "VERY_BULLISH", min(1.0, score)
    if score > 0.15: return "BULLISH", min(1.0, score)
    if score < -0.75: return "VERY_BEARISH", max(-1.0, score)
    if score < -0.15: return "BEARISH", max(-1.0, score)
    return "NEUTRAL", score
