"""Typed JSON only: never deserialize executable Python objects from the network."""
from datetime import datetime
from types import SimpleNamespace

import numpy as np


READ_METHODS = frozenset({
    "account_info", "terminal_info", "symbol_info", "symbols_get", "symbol_info_tick",
    "positions_get", "orders_get", "history_deals_get", "history_orders_get",
    "copy_rates_from", "copy_rates_from_pos", "copy_rates_range", "copy_ticks_from",
    "copy_ticks_range", "market_book_add", "market_book_get", "market_book_release",
    "order_check", "order_calc_margin", "order_calc_profit", "ensure_symbol", "timeframe",
})


def encode(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Bridge datetime must be timezone-aware")
        return {"$datetime": value.isoformat()}
    if isinstance(value, np.ndarray):
        if value.dtype.names is None or value.dtype.hasobject:
            raise ValueError("Only structured numeric MT5 arrays are supported")
        return {"$array": value.tolist(), "dtype": value.dtype.descr}
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "_asdict"):
        return {"$record": encode(value._asdict())}
    if isinstance(value, SimpleNamespace):
        return {"$record": encode(vars(value))}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Unsupported bridge value")


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$datetime" in value:
        result = datetime.fromisoformat(value["$datetime"])
        if result.tzinfo is None:
            raise ValueError("Bridge datetime must be timezone-aware")
        return result
    if "$record" in value:
        return SimpleNamespace(**decode(value["$record"]))
    if "$array" in value:
        dtype = np.dtype([tuple(field) for field in value["dtype"]])
        if dtype.hasobject or any(dtype[name].kind not in "iufb" for name in dtype.names or ()):
            raise ValueError("Only numeric MT5 fields are supported")
        return np.array([tuple(row) for row in value["$array"]], dtype=dtype)
    return {k: decode(v) for k, v in value.items()}
