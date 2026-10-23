from __future__ import annotations


def analyze_volume(df, cfg: dict | None = None) -> dict:
    x = df.iloc[-1]
    rv = float(x.get("relative_volume", 1.0) or 0.0)
    rising_obv = len(df) > 5 and float(df.obv.iloc[-1]) > float(df.obv.iloc[-5])
    score = 0.5
    reasons = []
    if rv >= 1.5: score += 0.30; reasons.append(f"Volume spike {rv:.2f}x")
    elif rv >= 1.0: score += 0.15; reasons.append(f"Volume above average {rv:.2f}x")
    elif rv < 0.7: score -= 0.20; reasons.append(f"Weak volume {rv:.2f}x")
    if rising_obv: score += 0.08; reasons.append("OBV rising")
    else: score -= 0.08; reasons.append("OBV not rising")
    return {"score": max(0.0, min(1.0, score)), "relative_volume": rv, "rising_obv": rising_obv, "reasons": reasons}
