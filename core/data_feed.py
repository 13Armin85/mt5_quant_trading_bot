from __future__ import annotations

from datetime import datetime, timezone
import math
import time

import pandas as pd
from loguru import logger
from core.market_hours import quote_session


class DataFeed:
    def __init__(self, client, max_tick_age_seconds: int = 15, symbol_config=None) -> None:
        self.client = client
        self.max_tick_age_seconds = max_tick_age_seconds
        self.symbol_config = symbol_config or {}
        self._last_quote_log = {}

    def _quote_schedule(self, symbol):
        for logical,meta in self.symbol_config.get("symbols",{}).items():
            if symbol==logical or symbol==meta.get("broker_symbol") or symbol in meta.get("aliases",[]):
                return meta.get("quote_schedule")
        return None

    def tick_status(self, symbol, now=None):
        now = now or datetime.now(timezone.utc)
        session = quote_session(self._quote_schedule(symbol),now)
        tick = self.tick(symbol)
        result = {"symbol":symbol,"fresh":False,"status":"MISSING_TICK","reason":"No quote received from terminal",
                  "last_tick_utc":None,"age_seconds":None,"bid":None,"ask":None,**session}
        if tick is not None:
            values = (float(tick.time),float(tick.bid),float(tick.ask))
            if not all(math.isfinite(v) and v>0 for v in values) or tick.ask < tick.bid:
                result.update(status="INVALID_TICK",reason="Invalid quote timestamp or Bid/Ask")
                return result
            stamp = datetime.fromtimestamp(float(tick.time),timezone.utc)
            age = (now-stamp).total_seconds()
            result.update(last_tick_utc=stamp.isoformat(),age_seconds=round(age,3),bid=float(tick.bid),ask=float(tick.ask))
            if age < -2:
                result.update(status="CLOCK_MISMATCH",reason="Quote is in the future; check trading.mt5_time_zone and system clock")
                return result
            if not session["scheduled_closed"]:
                if age <= self.max_tick_age_seconds:
                    result.update(status="FRESH",fresh=True,reason="Fresh broker quote")
                else:
                    result.update(status="STALE_TICK",reason="No fresh quote; check terminal data connection and instrument session")
        if session["scheduled_closed"]:
            result.update(status="MARKET_CLOSED",reason="Market closed according to configured weekly quote schedule; waiting for reopening and a fresh tick")
        return result

    def log_tick_status(self, status):
        symbol,code = status["symbol"],status["status"]
        previous = self._last_quote_log.get(symbol)
        now = time.monotonic()
        if previous and previous[0]==code and now-previous[1]<60:
            return
        self._last_quote_log[symbol] = (code,now)
        if code=="FRESH":
            return
        log = logger.info if code=="MARKET_CLOSED" else logger.warning
        log("quote_status symbol={} status={} last_tick_utc={} age_seconds={} next_open_tehran={} reason={}",
            symbol,code,status["last_tick_utc"],status["age_seconds"],status.get("next_open_tehran"),status["reason"])

    def bars(self, symbol: str, timeframe: str, count: int = 700, require_fresh: bool = True) -> pd.DataFrame:
        tf = self.client.timeframe(timeframe)
        rates = self.client.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        self.validate_bars(df, count)
        seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}[timeframe]
        age = (pd.Timestamp.now(tz="UTC") - df.time.iloc[-1]).total_seconds()
        if require_fresh and not seconds <= age <= seconds * 2 + self.max_tick_age_seconds:
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
        status = self.tick_status(symbol)
        self.log_tick_status(status)
        return status["fresh"]

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
