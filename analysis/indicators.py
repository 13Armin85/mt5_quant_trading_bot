from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    close, high, low = out["close"], out["high"], out["low"]
    volume = out.get("real_volume", out.get("tick_volume", pd.Series(index=out.index, dtype=float))).astype(float)
    if volume.fillna(0).sum() <= 0 and "tick_volume" in out:
        volume = out["tick_volume"].astype(float)
    for p in (20, 50, 100, 200):
        out[f"ema_{p}"] = EMAIndicator(close, p).ema_indicator()
    out["sma_20"] = SMAIndicator(close, 20).sma_indicator()
    out["rsi"] = RSIIndicator(close, int(cfg.get("rsi_period", 14))).rsi()
    macd = MACD(close)
    out["macd"] = macd.macd(); out["macd_signal"] = macd.macd_signal(); out["macd_hist"] = macd.macd_diff()
    adx = ADXIndicator(high, low, close, int(cfg.get("adx_period", 14)))
    out["adx"] = adx.adx(); out["adx_pos"] = adx.adx_pos(); out["adx_neg"] = adx.adx_neg()
    atr = AverageTrueRange(high, low, close, int(cfg.get("atr_period", 14)))
    out["atr"] = atr.average_true_range()
    bb = BollingerBands(close, int(cfg.get("bollinger_period", 20)))
    out["bb_high"] = bb.bollinger_hband(); out["bb_mid"] = bb.bollinger_mavg(); out["bb_low"] = bb.bollinger_lband(); out["bb_width"] = bb.bollinger_wband()
    stoch = StochRSIIndicator(close)
    out["stoch_rsi"] = stoch.stochrsi(); out["stoch_rsi_k"] = stoch.stochrsi_k(); out["stoch_rsi_d"] = stoch.stochrsi_d()
    out["obv"] = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    out["volume"] = volume
    out["volume_sma"] = volume.rolling(int(cfg.get("volume_sma_period", 20))).mean()
    out["relative_volume"] = volume / out["volume_sma"].replace(0, np.nan)
    try:
        out["vwap"] = VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price()
    except Exception:
        out["vwap"] = np.nan
    out["return_1"] = close.pct_change()
    out["return_5"] = close.pct_change(5)
    return out
