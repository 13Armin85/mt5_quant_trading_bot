from __future__ import annotations

import pandas as pd

FEATURE_COLUMNS = [
    "rsi", "macd", "macd_signal", "macd_hist", "adx", "atr", "return_1", "return_5",
    "relative_volume", "bb_width", "stoch_rsi", "ema20_dist", "ema50_dist", "ema200_dist",
    "candle_body_pct", "upper_wick_pct", "lower_wick_pct", "hour_sin", "hour_cos",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["ema20_dist"] = (x.close - x.ema_20) / x.close
    x["ema50_dist"] = (x.close - x.ema_50) / x.close
    x["ema200_dist"] = (x.close - x.ema_200) / x.close
    rng = (x.high - x.low).replace(0, pd.NA)
    x["candle_body_pct"] = (x.close - x.open).abs() / rng
    x["upper_wick_pct"] = (x.high - x[["open", "close"]].max(axis=1)) / rng
    x["lower_wick_pct"] = (x[["open", "close"]].min(axis=1) - x.low) / rng
    hour = pd.to_datetime(x.time, utc=True).dt.hour
    import numpy as np
    x["hour_sin"] = np.sin(2*np.pi*hour/24); x["hour_cos"] = np.cos(2*np.pi*hour/24)
    return x[FEATURE_COLUMNS].replace([float("inf"), float("-inf")], pd.NA).dropna()


def make_tp_before_sl_target(df: pd.DataFrame, horizon: int = 48, atr_multiple: float = 1.5, rr: float = 2.0) -> pd.Series:
    target = pd.Series(index=df.index, dtype="float64")
    for i in range(len(df)-horizon-1):
        entry=float(df.close.iloc[i]); atr=float(df.atr.iloc[i]); sl=entry-atr*atr_multiple; tp=entry+atr*atr_multiple*rr
        future=df.iloc[i+1:i+1+horizon]
        y=None
        for b in future.itertuples():
            if b.low <= sl: y=0; break
            if b.high >= tp: y=1; break
        target.iloc[i]=y if y is not None else 0
    return target
