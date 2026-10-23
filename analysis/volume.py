from __future__ import annotations


def analyze_volume(df, cfg: dict | None = None) -> dict:
    x = df.iloc[-1]
    rv = float(x.get("relative_volume", 1.0) or 0.0)
    rising_obv = len(df) > 5 and float(df.obv.iloc[-1]) > float(df.obv.iloc[-5])
    falling_obv = len(df) > 5 and float(df.obv.iloc[-1]) < float(df.obv.iloc[-5])
    score = 0.5
    reasons = []
    if rv >= 1.5: score += 0.30; reasons.append(f"Volume spike {rv:.2f}x")
    elif rv >= 1.0: score += 0.15; reasons.append(f"Volume above average {rv:.2f}x")
    elif rv < 0.7: score -= 0.20; reasons.append(f"Weak volume {rv:.2f}x")
    if rising_obv: reasons.append("OBV rising")
    elif falling_obv: reasons.append("OBV falling")
    else: reasons.append("OBV flat/insufficient history")
    return {"score": max(0.0, min(1.0, score)), "relative_volume": rv, "rising_obv": rising_obv,
            "flow_score":.65 if rising_obv else .35 if falling_obv else .5, "reasons": reasons}
