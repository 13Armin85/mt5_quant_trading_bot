from __future__ import annotations

from datetime import datetime, timezone
import math

import pandas as pd
from loguru import logger


class DataFeed:
    def __init__(self, client, max_tick_age_seconds: int = 15) -> None:
        self.client = client
        self.max_tick_age_seconds = max_tick_age_seconds

    def bars(self, symbol: str, timeframe: str, count: int = 700) -> pd.DataFrame:
        tf = self.client.timeframe(timeframe)
        rates = self.client.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        self.validate_bars(df, count)
        seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[timeframe]
        age = (pd.Timestamp.now(tz="UTC") - df.time.iloc[-1]).total_seconds()
        if not seconds <= age <= seconds * 2 + self.max_tick_age_seconds:
            raise ValueError(f"{symbol} {timeframe}: stale or unclosed bar")
        return df

    @staticmethod
    def validate_bars(df, minimum=1):
        required = {"time", "open", "high", "low", "close"}
        if not required.issubset(df.columns) or len(df) < minimum:
            raise ValueError("Incomplete OHLC history")
        if df.time.isna().any() or df.time.duplicated().any() or not df.time.is_monotonic_increasing:
            raise ValueError("Duplicate, invalid or unordered bar times")
        values = df[["open", "high", "low", "close"]]
        import numpy as np
        if not np.isfinite(values.to_numpy(dtype=float)).all() or (values <= 0).any().any():
            raise ValueError("Invalid OHLC prices")
        if ((df.high < values.max(axis=1)) | (df.low > values.min(axis=1))).any():
            raise ValueError("Inconsistent OHLC range")
        for column in ("tick_volume", "real_volume"):
            if column in df and (not np.isfinite(df[column].to_numpy(dtype=float)).all() or (df[column] < 0).any()):
                raise ValueError("Invalid volume")

    def tick(self, symbol: str):
        return self.client.symbol_info_tick(symbol)

    def is_tick_fresh(self, symbol: str) -> bool:
        tick = self.tick(symbol)
        if tick is None:
            return False
        tick_time = datetime.fromtimestamp(float(tick.time), tz=timezone.utc)
        age = (datetime.now(timezone.utc) - tick_time).total_seconds()
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in (tick.bid, tick.ask)) or tick.ask < tick.bid:
            return False
        if age < -2:
            logger.warning("Future tick {} offset={}s; check trading.mt5_time_zone and system clock", symbol, -age)
            return False
        if age > self.max_tick_age_seconds:
            logger.warning("Stale tick {} age={}s", symbol, age)
            return False
        return True

    def spread_points(self, symbol: str) -> float | None:
        tick = self.tick(symbol)
        info = self.client.symbol_info(symbol)
        if tick is None or info is None or not info.point:
            return None
        return (tick.ask - tick.bid) / info.point

    def market_depth_imbalance(self, symbol: str) -> float | None:
        try:
            if not self.client.market_book_add(symbol):
                return None
            book = self.client.market_book_get(symbol)
            if not book:
                return None
            bid = sum(float(x.volume_dbl or x.volume) for x in book if int(x.type) in (1, 3))
            ask = sum(float(x.volume_dbl or x.volume) for x in book if int(x.type) in (2, 4))
            total = bid + ask
            return None if total <= 0 else (bid - ask) / total
        finally:
            try:
                self.client.market_book_release(symbol)
            except Exception:
                pass
