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


def make_tp_before_sl_target(df: pd.DataFrame, horizon: int = 48, atr_multiple: float = 1.5, rr: float = 2.0,
                            *, direction="BUY", spread=0., slippage=0., commission_per_lot=0., contract_value=1., timeout="exclude") -> pd.Series:
    import math
    if horizon < 1 or direction not in {"BUY","SELL"} or timeout not in {"exclude","net_return"}:
        raise ValueError("Invalid label horizon/direction/timeout policy")
    if not all(math.isfinite(v) for v in (atr_multiple,rr,spread,slippage,commission_per_lot,contract_value)) or min(atr_multiple,rr,contract_value)<=0 or min(spread,slippage,commission_per_lot)<0:
        raise ValueError("Invalid label costs/geometry")
    if not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError("Labels require unique ordered source rows")
    times = pd.to_datetime(df.time,utc=True)
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("Labels require ordered source timestamps")
    target = pd.Series(index=df.index, dtype="float64")
    starts = pd.Series(pd.NaT,index=df.index,dtype="datetime64[ns, UTC]")
    ends = starts.copy()
    sign = 1 if direction=="BUY" else -1
    for i in range(len(df)-horizon-1):
        entry = float(df.open.iloc[i+1])+(spread if sign==1 else 0)+sign*slippage
        atr = float(df.atr.iloc[i])
        if not math.isfinite(atr) or atr<=0:
            continue
        sl, tp = entry-sign*atr*atr_multiple, entry+sign*atr*atr_multiple*rr
        exit_price, end = None, i+horizon
        for j in range(i+1,i+horizon+1):
            b = df.iloc[j]
            high, low, opened = float(b.high),float(b.low),float(b.open)
            if sign==-1:
                high += spread; low += spread; opened += spread
            if (low<=sl if sign==1 else high>=sl):
                exit_price = min(sl,opened) if sign==1 else max(sl,opened)
            elif (high>=tp if sign==1 else low<=tp):
                exit_price = tp
            if exit_price is not None:
                end = j; break
        if exit_price is None and timeout=="net_return":
            exit_price = float(df.close.iloc[end])+(spread if sign==-1 else 0)
        if exit_price is not None:
            pnl = sign*(exit_price-sign*slippage-entry)*contract_value-commission_per_lot
            target.iloc[i] = int(pnl>0)
        starts.iloc[i], ends.iloc[i] = times.iloc[i+1], times.iloc[end+1]
    target.attrs.update(label_start=starts,label_end=ends,horizon_bars=horizon,timeout_policy=timeout,
        direction=direction,label_basis="Next-bar fill; SL first collisions; after explicit costs")
    return target
