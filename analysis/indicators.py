from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochRSIIndicator, StochasticOscillator, WilliamsRIndicator, ROCIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD, SMAIndicator, CCIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator


def _supertrend(high, low, close, atr, multiple):
    """Trailing bands depend only on the current and preceding bars."""
    midpoint = (high.to_numpy() + low.to_numpy()) / 2
    prices, volatility = close.to_numpy(), atr.to_numpy()
    upper, lower = midpoint + multiple * volatility, midpoint - multiple * volatility
    line = np.full(len(close), np.nan)
    direction = np.full(len(close), np.nan)
    for i in range(len(close)):
        if not np.isfinite(volatility[i]) or volatility[i] <= 0:
            continue
        if i == 0 or not np.isfinite(line[i-1]):
            direction[i] = 1 if prices[i] >= midpoint[i] else -1
        else:
            upper[i] = upper[i] if upper[i] < upper[i-1] or prices[i-1] > upper[i-1] else upper[i-1]
            lower[i] = lower[i] if lower[i] > lower[i-1] or prices[i-1] < lower[i-1] else lower[i-1]
            if direction[i-1] == -1:
                direction[i] = 1 if prices[i] > upper[i] else -1
            else:
                direction[i] = -1 if prices[i] < lower[i] else 1
        line[i] = lower[i] if direction[i] == 1 else upper[i]
    return line, direction


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    close, high, low = out["close"], out["high"], out["low"]
    volume = out.get("real_volume", out.get("tick_volume", pd.Series(index=out.index, dtype=float))).astype(float)
    if volume.fillna(0).sum() <= 0 and "tick_volume" in out:
        volume = out["tick_volume"].astype(float)
    for p in (20, 50, 100, 200):
        out[f"ema_{p}"] = EMAIndicator(close, p).ema_indicator()
    for column, key, default in (("ema_20", "ema_fast", 20), ("ema_50", "ema_mid", 50), ("ema_200", "ema_slow", 200)):
        out[column] = EMAIndicator(close, int(cfg.get(key, default))).ema_indicator()
    out["sma_20"] = SMAIndicator(close, 20).sma_indicator()
    out["rsi"] = RSIIndicator(close, int(cfg.get("rsi_period", 14))).rsi()
    macd = MACD(close, window_slow=int(cfg.get("macd_slow", 26)), window_fast=int(cfg.get("macd_fast", 12)), window_sign=int(cfg.get("macd_signal", 9)))
    out["macd"] = macd.macd(); out["macd_signal"] = macd.macd_signal(); out["macd_hist"] = macd.macd_diff()
    adx = ADXIndicator(high, low, close, int(cfg.get("adx_period", 14)))
    out["adx"] = adx.adx(); out["adx_pos"] = adx.adx_pos(); out["adx_neg"] = adx.adx_neg()
    atr = AverageTrueRange(high, low, close, int(cfg.get("atr_period", 14)))
    out["atr"] = atr.average_true_range()
    bb = BollingerBands(close, int(cfg.get("bollinger_period", 20)))
    out["bb_high"] = bb.bollinger_hband(); out["bb_mid"] = bb.bollinger_mavg(); out["bb_low"] = bb.bollinger_lband(); out["bb_width"] = bb.bollinger_wband()
    stoch = StochRSIIndicator(close, window=int(cfg.get("rsi_period", 14)))
    out["stoch_rsi"] = stoch.stochrsi(); out["stoch_rsi_k"] = stoch.stochrsi_k(); out["stoch_rsi_d"] = stoch.stochrsi_d()
    out["obv"] = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    out["volume"] = volume
    out["volume_sma"] = volume.rolling(int(cfg.get("volume_sma_period", 20))).mean()
    out["relative_volume"] = volume / out["volume_sma"].replace(0, np.nan)
    vwap_period = int(cfg.get("vwap_period", 20))
    typical = (high + low + close) / 3
    out["vwap"] = (typical * volume).rolling(vwap_period).sum() / volume.rolling(vwap_period).sum().replace(0, np.nan)
    # This is a rolling VWAP, not exchange-wide or session-anchored volume.
    oscillator = StochasticOscillator(high, low, close, window=int(cfg.get("stochastic_period", 14)), smooth_window=int(cfg.get("stochastic_smooth", 3)))
    out["stoch_k"], out["stoch_d"] = oscillator.stoch(), oscillator.stoch_signal()
    out["cci"] = CCIIndicator(high, low, close, window=int(cfg.get("cci_period", 20))).cci()
    out["williams_r"] = WilliamsRIndicator(high, low, close, lbp=int(cfg.get("williams_period", 14))).williams_r()
    out["roc"] = ROCIndicator(close, window=int(cfg.get("roc_period", 12))).roc()
    donchian = int(cfg.get("donchian_period", 20))
    out["donchian_high"] = high.rolling(donchian).max().shift(1)
    out["donchian_low"] = low.rolling(donchian).min().shift(1)
    out["keltner_mid"] = EMAIndicator(close, int(cfg.get("keltner_period", 20))).ema_indicator()
    out["keltner_high"], out["keltner_low"] = out.keltner_mid + 2 * out.atr, out.keltner_mid - 2 * out.atr
    fast, base, span = (int(cfg.get(k, d)) for k, d in (("ichimoku_fast", 9), ("ichimoku_base", 26), ("ichimoku_span", 52)))
    out["tenkan"] = (high.rolling(fast).max() + low.rolling(fast).min()) / 2
    out["kijun"] = (high.rolling(base).max() + low.rolling(base).min()) / 2
    # At time t the plotted cloud is built at t-base. Never shift price backward.
    out["cloud_a"] = ((out.tenkan + out.kijun) / 2).shift(base)
    out["cloud_b"] = ((high.rolling(span).max() + low.rolling(span).min()) / 2).shift(base)
    out["supertrend"], out["supertrend_direction"] = _supertrend(high, low, close, out.atr, float(cfg.get("supertrend_multiple", 3)))
    out["return_1"] = close.pct_change()
    out["return_5"] = close.pct_change(5)
    return out
