from __future__ import annotations

import numpy as np


def _swings(df, window: int = 3):
    if window < 1:
        raise ValueError("Swing window must be positive")
    if len(df) < window * 2 + 1:
        return [], []
    high, low = df.high.to_numpy(), df.low.to_numpy()
    from numpy.lib.stride_tricks import sliding_window_view
    high_windows = sliding_window_view(high, window * 2 + 1)
    low_windows = sliding_window_view(low, window * 2 + 1)
    hi = np.flatnonzero(high[window:-window] >= high_windows.max(axis=1)) + window
    lo = np.flatnonzero(low[window:-window] <= low_windows.min(axis=1)) + window
    highs = [(int(i), float(high[i])) for i in hi]
    lows = [(int(i), float(low[i])) for i in lo]
    return highs, lows


def analyze_structure(df) -> dict:
    highs, lows = _swings(df.tail(250).reset_index(drop=True))
    direction = "RANGE"; score = 0.5; reasons = []
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]; hl = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]; ll = lows[-1][1] < lows[-2][1]
        if hh and hl:
            direction = "BULLISH"; score = 0.85; reasons += ["Higher High", "Higher Low"]
        elif lh and ll:
            direction = "BEARISH"; score = 0.15; reasons += ["Lower High", "Lower Low"]
    recent = df.tail(30)
    prior_high = float(recent.high.iloc[:-1].max()) if len(recent) > 1 else np.nan
    prior_low = float(recent.low.iloc[:-1].min()) if len(recent) > 1 else np.nan
    close = float(recent.close.iloc[-1])
    breakout = "UP" if close > prior_high else "DOWN" if close < prior_low else None
    if breakout: reasons.append(f"Breakout {breakout}")
    return {"direction": direction, "score": score, "breakout": breakout, "swing_highs": highs[-5:], "swing_lows": lows[-5:], "reasons": reasons}
