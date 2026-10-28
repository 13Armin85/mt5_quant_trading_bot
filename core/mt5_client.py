from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any
from types import SimpleNamespace
import time

from loguru import logger

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None


class MT5Error(RuntimeError):
    pass


class MT5Client:
    def __init__(self, env, market_timezone: str = "UTC") -> None:
        self.env = env
        self.connected = False
        self.account_identity = None
        self.market_timezone = ZoneInfo(market_timezone)

    def market_timestamp_to_utc(self, timestamp: float) -> float:
        if self.market_timezone.key == "UTC":
            return timestamp
        wall_time = datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
        return wall_time.replace(tzinfo=self.market_timezone).timestamp()

    def utc_to_market_time(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("MT5 date arguments must be timezone-aware")
        return value.astimezone(self.market_timezone).replace(tzinfo=timezone.utc)

    def normalize_market_times(self, rows):
        if rows is None or len(rows) == 0 or self.market_timezone.key == "UTC":
            return rows
        import pandas as pd
        normalized = rows.copy()
        for field, unit in (("time", "s"), ("time_msc", "ms")):
            if field in (rows.dtype.names or ()):
                naive = pd.to_datetime(rows[field], unit=unit)
                try:
                    localized = naive.tz_localize(self.market_timezone, ambiguous="infer", nonexistent="raise")
                except ValueError:
                    # A single bar in the repeated DST hour has no observable fold.
                    # Place it at the later occurrence so historical data cannot
                    # appear before it was available. Tick arrays remain strict.
                    if "open" not in rows.dtype.names:
                        raise
                    probe = naive.tz_localize(self.market_timezone, ambiguous="NaT", nonexistent="raise")
                    if not probe.isna().any():
                        raise
                    localized = naive.tz_localize(self.market_timezone, ambiguous=False, nonexistent="raise")
                values = localized.tz_convert("UTC")
                normalized[field] = values.as_unit(unit).asi8
        return normalized

    def initialize(self) -> bool:
        if mt5 is None:
            raise MT5Error("MetaTrader5 package is unavailable on this platform.")
        kwargs: dict[str, Any] = {}
        if self.env.mt5_path:
            kwargs["path"] = self.env.mt5_path
        if self.env.mt5_login:
            kwargs["login"] = int(self.env.mt5_login)
        if self.env.mt5_password:
            kwargs["password"] = self.env.mt5_password
        if self.env.mt5_server:
            kwargs["server"] = self.env.mt5_server
        self.connected = bool(mt5.initialize(**kwargs))
        if not self.connected:
            raise MT5Error(f"MT5 initialize failed (code {mt5.last_error()[0]})")
        account = mt5.account_info()
        if account is None or not self.health_check():
            self.shutdown()
            raise MT5Error("MT5 account/terminal unavailable")
        if self.env.mt5_login and int(account.login) != int(self.env.mt5_login):
            self.shutdown()
            raise MT5Error("Connected account differs from MT5_LOGIN")
        if self.env.mt5_server and account.server != self.env.mt5_server:
            self.shutdown()
            raise MT5Error("Connected server differs from MT5_SERVER")
        identity = (int(account.login), str(account.server))
        if self.account_identity is not None and identity != self.account_identity:
            self.shutdown()
            raise MT5Error("Account changed during execution; restart with intended account")
        self.account_identity = identity
        logger.info("MT5 connected")
        return True

    def shutdown(self) -> None:
        if mt5 is not None:
            mt5.shutdown()
        self.connected = False

    def reconnect(self, attempts=3, delay=5) -> bool:
        for attempt in range(attempts):
            self.shutdown()
            try:
                return self.initialize()
            except MT5Error:
                logger.warning("MT5 reconnect attempt {} failed", attempt + 1)
                if attempt + 1 < attempts:
                    time.sleep(delay)
        raise MT5Error("MT5 reconnect attempts exhausted")

    def health_check(self) -> bool:
        if mt5 is None or not self.connected:
            return False
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        return bool(terminal and account and getattr(terminal, "connected", False)
                    and (self.account_identity is None or (int(account.login), str(account.server)) == self.account_identity))

    def account_info(self):
        return self._call(mt5.account_info)

    def terminal_info(self):
        return self._call(mt5.terminal_info)

    def symbol_info(self, symbol: str):
        return self._call(mt5.symbol_info, symbol)

    def symbols_get(self):
        return self._call(mt5.symbols_get)

    def symbol_info_tick(self, symbol: str):
        tick = self._call(mt5.symbol_info_tick, symbol)
        if tick is None or self.market_timezone.key == "UTC":
            return tick
        fields = tick._asdict()
        fields.update(time=int(self.market_timestamp_to_utc(tick.time)),
                      time_msc=round(self.market_timestamp_to_utc(tick.time_msc / 1000) * 1000))
        return SimpleNamespace(**fields)

    def positions_get(self, **kwargs):
        return self._call(mt5.positions_get, **kwargs)

    def orders_get(self, **kwargs):
        return self._call(mt5.orders_get, **kwargs)

    def history_deals_get(self, *args, **kwargs):
        args = tuple(self.utc_to_market_time(a) if isinstance(a, datetime) else a for a in args)
        return self._call(mt5.history_deals_get, *args, **kwargs)

    def history_orders_get(self, *args, **kwargs):
        args = tuple(self.utc_to_market_time(a) if isinstance(a, datetime) else a for a in args)
        return self._call(mt5.history_orders_get, *args, **kwargs)

    def copy_rates_from(self, symbol: str, timeframe: int, date_from: datetime, count: int):
        return self.normalize_market_times(self._call(mt5.copy_rates_from, symbol, timeframe, self.utc_to_market_time(date_from), count))

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        return self.normalize_market_times(self._call(mt5.copy_rates_from_pos, symbol, timeframe, start_pos, count))

    def copy_rates_range(self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime):
        return self.normalize_market_times(self._call(mt5.copy_rates_range, symbol, timeframe, self.utc_to_market_time(date_from), self.utc_to_market_time(date_to)))

    def copy_ticks_from(self, symbol: str, date_from: datetime, count: int, flags: int):
        return self.normalize_market_times(self._call(mt5.copy_ticks_from, symbol, self.utc_to_market_time(date_from), count, flags))

    def copy_ticks_range(self, symbol: str, date_from: datetime, date_to: datetime, flags: int):
        return self.normalize_market_times(self._call(mt5.copy_ticks_range, symbol, self.utc_to_market_time(date_from), self.utc_to_market_time(date_to), flags))

    def market_book_add(self, symbol: str):
        return self._call(mt5.market_book_add, symbol)

    def market_book_get(self, symbol: str):
        return self._call(mt5.market_book_get, symbol)

    def market_book_release(self, symbol: str):
        return self._call(mt5.market_book_release, symbol)

    def order_check(self, request: dict):
        return self._call(mt5.order_check, request)

    def order_calc_margin(self, order_type: int, symbol: str, volume: float, price: float):
        return self._call(mt5.order_calc_margin, order_type, symbol, volume, price)

    def order_calc_profit(self, order_type: int, symbol: str, volume: float, price_open: float, price_close: float):
        return self._call(mt5.order_calc_profit, order_type, symbol, volume, price_open, price_close)

    def order_send(self, request: dict):
        return self._call(mt5.order_send, request)

    def ensure_symbol(self, symbol: str) -> bool:
        info = self.symbol_info(symbol)
        if info is None:
            return False
        if not getattr(info, "visible", True):
            return bool(mt5.symbol_select(symbol, True))
        return True

    def timeframe(self, name: str) -> int:
        mapping = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        if name not in mapping:
            raise ValueError(f"Unsupported timeframe {name}")
        return mapping[name]

    def _call(self, fn, *args, **kwargs):
        if not self.health_check():
            raise MT5Error("MT5 connection is not healthy")
        # MetaTrader5's native order_check/order_send reject an empty keyword
        # expansion as "Unnamed arguments not allowed". Call positional-only
        # methods without **{} so the C extension receives the expected form.
        result = fn(*args, **kwargs) if kwargs else fn(*args)
        if result is None:
            code = mt5.last_error() if mt5 else None
            logger.error("MT5 call {} failed code={}", getattr(fn, "__name__", fn), code[0] if code else None)
        return result
