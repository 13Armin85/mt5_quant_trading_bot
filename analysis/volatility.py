from __future__ import annotations


def score_volatility(df) -> tuple[float, list[str]]:
    x = df.iloc[-1]
    atr_pct = float(x.atr / x.close) if x.close else 0.0
    if 0.0005 <= atr_pct <= 0.02:
        return 0.8, [f"ATR% acceptable {atr_pct:.4%}"]
    if atr_pct > 0.04:
        return 0.15, [f"Excessive volatility {atr_pct:.4%}"]
    return 0.45, [f"Low/atypical volatility {atr_pct:.4%}"]
