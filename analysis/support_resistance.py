from __future__ import annotations


def levels(df, lookback: int = 100) -> dict:
    x = df.tail(lookback)
    prev_day = df.iloc[:-1].tail(288) if len(df) > 288 else df.iloc[:-1]
    return {
        "support": float(x.low.quantile(0.1)),
        "resistance": float(x.high.quantile(0.9)),
        "previous_period_high": float(prev_day.high.max()) if not prev_day.empty else None,
        "previous_period_low": float(prev_day.low.min()) if not prev_day.empty else None,
    }
