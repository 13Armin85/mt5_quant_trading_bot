from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover
    mt5 = None


class MT5Error(RuntimeError):
    pass


class MT5Client:
    def __init__(self, env) -> None:
        self.env = env
        self.connected = False

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
            raise MT5Error(f"MT5 initialize failed: {mt5.last_error()}")
        logger.info("MT5 connected")
        return True

    def shutdown(self) -> None:
        if mt5 is not None:
            mt5.shutdown()
        self.connected = False

    def reconnect(self) -> bool:
        self.shutdown()
        return self.initialize()

    def health_check(self) -> bool:
        if mt5 is None or not self.connected:
            return False
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        return bool(terminal and account and getattr(terminal, "connected", True))

    def account_info(self):
        return self._call(mt5.account_info)

    def terminal_info(self):
        return self._call(mt5.terminal_info)

    def symbol_info(self, symbol: str):
        return self._call(mt5.symbol_info, symbol)

    def symbol_info_tick(self, symbol: str):
        return self._call(mt5.symbol_info_tick, symbol)

    def positions_get(self, **kwargs):
        return self._call(mt5.positions_get, **kwargs)

    def orders_get(self, **kwargs):
        return self._call(mt5.orders_get, **kwargs)

    def copy_rates_from(self, symbol: str, timeframe: int, date_from: datetime, count: int):
        return self._call(mt5.copy_rates_from, symbol, timeframe, date_from, count)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        return self._call(mt5.copy_rates_from_pos, symbol, timeframe, start_pos, count)

    def copy_rates_range(self, symbol: str, timeframe: int, date_from: datetime, date_to: datetime):
        return self._call(mt5.copy_rates_range, symbol, timeframe, date_from, date_to)

    def copy_ticks_from(self, symbol: str, date_from: datetime, count: int, flags: int):
        return self._call(mt5.copy_ticks_from, symbol, date_from, count, flags)

    def copy_ticks_range(self, symbol: str, date_from: datetime, date_to: datetime, flags: int):
        return self._call(mt5.copy_ticks_range, symbol, date_from, date_to, flags)

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
        if not self.health_check() and fn not in {getattr(mt5, "account_info", None), getattr(mt5, "terminal_info", None)}:
            raise MT5Error("MT5 connection is not healthy")
        result = fn(*args, **kwargs)
        if result is None:
            code = mt5.last_error() if mt5 else None
            logger.error("MT5 call {} failed: {}", getattr(fn, "__name__", fn), code)
        return result
