from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
import math
import time

import pandas as pd
from loguru import logger
from core.market_hours import quote_session,open_seconds_between,bar_close_utc
from signals.filters import assess_spread

TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                     "H1": 3600, "H4": 14400, "D1": 86400}


class DataFeed:
    def __init__(self, client, max_tick_age_seconds: int = 15, symbol_config=None, *, clock=None) -> None:
        self.client = client
        self.max_tick_age_seconds = max_tick_age_seconds
        self.symbol_config = symbol_config or {}
        self._last_quote_log = {}
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self._spread_samples={}

    def _quote_schedule(self, symbol):
        for logical,meta in self.symbol_config.get("symbols",{}).items():
            if symbol==logical or symbol==meta.get("broker_symbol") or symbol in meta.get("aliases",[]):
                return meta.get("quote_schedule")
        return None

    def tick_status(self, symbol, now=None):
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError("Tick freshness requires timezone-aware UTC time")
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
            from zoneinfo import ZoneInfo
            result["last_tick_tehran"]=stamp.astimezone(ZoneInfo("Asia/Tehran")).isoformat()
            result["last_tick_server"]=stamp.astimezone(getattr(self.client,"market_timezone",timezone.utc)).isoformat()
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

    def bars(self, symbol: str, timeframe: str, count: int = 700, require_fresh: bool = True, allow_session_gaps=False) -> pd.DataFrame:
        tf = self.client.timeframe(timeframe)
        rates = self.client.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        self.validate_bars(df, count)
        seconds = TIMEFRAME_SECONDS[timeframe]
        now=self.clock()
        if now.tzinfo is None:
            raise ValueError("Bar freshness requires timezone-aware UTC time")
        closed=bar_close_utc(df.time.iloc[-1].to_pydatetime(),seconds,getattr(self.client,"market_timezone",timezone.utc))
        age=(now-closed).total_seconds()
        if allow_session_gaps and age>=0:
            age=open_seconds_between(self._quote_schedule(symbol),closed,now)
        if require_fresh and (now<closed or age>seconds+self.max_tick_age_seconds):
            raise ValueError(f"{symbol} {timeframe}: stale or unclosed bar")
        df.attrs.update(last_bar_closed_at=closed.isoformat(),freshness_basis="trading_time" if allow_session_gaps else "wall_clock")
        return df

    def live_chart_status(self, symbol, timeframe, closed_frame, quote_status, indicator_cfg=None):
        """Read MT5's forming bar for display; only closed_frame drives signals.

        A missing forming bar does not replace a valid broker tick. A bar that
        contradicts the closed-bar snapshot does block the current evaluation.
        """
        seconds = TIMEFRAME_SECONDS[timeframe]
        closed = closed_frame.iloc[-1]
        closed_open = pd.Timestamp(closed["time"]).to_pydatetime()
        expected_open = bar_close_utc(closed_open, seconds,
            getattr(self.client, "market_timezone", timezone.utc))
        result = {"source": "MT5", "timeframe": timeframe,
            "status": "UNAVAILABLE", "reason": "Current broker bar unavailable",
            "closed_bar_open_utc": closed_open.isoformat(),
            "closed_bar_closed_at_utc": expected_open.isoformat(),
            "tick_utc": quote_status.get("last_tick_utc"),
            "bid": quote_status.get("bid"), "ask": quote_status.get("ask"),
            "forming_bar": None, "provisional_indicators": {}}
        if not hasattr(self.client, "copy_rates_from_pos"):
            return result
        try:
            rates = self.client.copy_rates_from_pos(symbol, self.client.timeframe(timeframe), 0, 1)
        except (ValueError, RuntimeError, TimeoutError, ConnectionError):
            return result
        if rates is None or len(rates) != 1:
            return result
        bar = pd.DataFrame(rates)
        if "time" not in bar:
            result.update(status="INVALID_BAR", reason="Invalid forming MT5 bar")
            return result
        try:
            bar["time"] = pd.to_datetime(bar["time"], unit="s", utc=True)
            self.validate_bars(bar)
        except (TypeError, ValueError, OverflowError):
            result.update(status="INVALID_BAR", reason="Invalid forming MT5 bar")
            return result
        row = bar.iloc[0]
        opened = row.time.to_pydatetime()
        closes_at = bar_close_utc(opened, seconds,
            getattr(self.client, "market_timezone", timezone.utc))
        result["forming_bar"] = {"open_utc": opened.isoformat(),
            "closes_at_utc": closes_at.isoformat(),
            **{name: float(row[name]) for name in ("open", "high", "low", "close")},
            "tick_volume": float(row.get("tick_volume", 0))}
        if opened != expected_open:
            result.update(status="OUT_OF_SYNC", reason="Forming bar and confirmed chart history disagree; retry next cycle")
            return result
        tick_time = quote_status.get("last_tick_utc")
        if tick_time and datetime.fromisoformat(tick_time) < opened:
            result.update(status="OUT_OF_SYNC", reason="Broker tick predates the forming bar; retry next cycle")
            return result
        if self.clock() >= closes_at:
            result.update(status="AWAITING_NEW_BAR", reason="Waiting for the first tick of the next broker bar")
            return result
        indicator_cfg = indicator_cfg or {}
        chart_price = float(row["close"])
        result["provisional_indicators"]["chart_price"] = chart_price
        for name, config_key, default in (("ema_20", "ema_fast", 20),
                                           ("ema_50", "ema_mid", 50),
                                           ("ema_200", "ema_slow", 200)):
            previous = float(closed.get(name, float("nan")))
            if math.isfinite(previous):
                period = int(indicator_cfg.get(config_key, default))
                result["provisional_indicators"][name] = previous + 2 / (period + 1) * (chart_price - previous)
        if all(name in closed for name in ("high", "low")):
            result["provisional_indicators"].update(
                above_last_high=chart_price > float(closed["high"]),
                below_last_low=chart_price < float(closed["low"]))
        result.update(status="LIVE", reason="Current MT5 bar and fresh broker quote observed; indicators are provisional")
        return result

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

    def spread_status(self,symbol,historical,multiplier,absolute_limit):
        tick,info=self.tick(symbol),self.client.symbol_info(symbol)
        now=self.clock().timestamp()
        samples=self._spread_samples.setdefault(symbol,deque(maxlen=600))
        while samples and samples[0][0]<now-300:
            samples.popleft()
        current=None
        valid=False
        if tick is not None and info is not None and math.isfinite(float(info.point)) and info.point>0:
            current=(float(tick.ask)-float(tick.bid))/float(info.point)
            valid=all(math.isfinite(float(v)) and v>0 for v in (tick.time,tick.bid,tick.ask)) and tick.ask>=tick.bid
            if not valid:
                current=None
        status=assess_spread(current,historical,multiplier,absolute_limit,[s[2] for s in samples])
        if valid and status["allowed"] and -2<=now-float(tick.time)<=self.max_tick_age_seconds:
            identity=(getattr(tick,"time_msc",tick.time),float(tick.bid),float(tick.ask))
            if not samples or samples[-1][1]!=identity:
                samples.append((float(tick.time),identity,current))
        return status

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
