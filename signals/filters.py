from __future__ import annotations

import hashlib
from datetime import datetime


def signal_id(symbol: str, timeframe: str, direction: str, candle_time: datetime | str, strategy: str) -> str:
    raw = f"{symbol}|{timeframe}|{direction}|{candle_time}|{strategy}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def spread_is_valid(current: float | None, historical: list[float] | None, multiplier: float = 2.0) -> tuple[bool, float]:
    if current is None or current < 0:
        return False, 0.0
    if not historical:
        return True, 0.7
    values = sorted(v for v in historical if v >= 0)
    if not values:
        return True, 0.7
    median = values[len(values) // 2]
    limit = max(median * multiplier, median + 1e-9)
    return current <= limit, max(0.0, min(1.0, 1.0 - current / max(limit * 1.5, 1e-9)))
