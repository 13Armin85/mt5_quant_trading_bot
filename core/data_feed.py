from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from loguru import logger


class DataFeed:
    def __init__(self, client, max_tick_age_seconds: int = 15) -> None:
        self.client = client
        self.max_tick_age_seconds = max_tick_age_seconds

    def bars(self, symbol: str, timeframe: str, count: int = 700) -> pd.DataFrame:
        tf = self.client.timeframe(timeframe)
        rates = self.client.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def tick(self, symbol: str):
        return self.client.symbol_info_tick(symbol)

    def is_tick_fresh(self, symbol: str) -> bool:
        tick = self.tick(symbol)
        if tick is None:
            return False
        tick_time = datetime.fromtimestamp(float(tick.time), tz=timezone.utc)
        age = (datetime.now(timezone.utc) - tick_time).total_seconds()
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
