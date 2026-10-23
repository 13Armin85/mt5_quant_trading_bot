from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger


@dataclass(slots=True)
class RiskState:
    day_start_equity: float
    week_start_equity: float
    consecutive_losses: int = 0


class RiskManager:
    def __init__(self, cfg: dict, client=None) -> None:
        self.cfg = cfg
        self.client = client
        self.state: RiskState | None = None

    def initialize_state(self, equity: float) -> None:
        self.state = RiskState(equity, equity)

    def risk_amount(self, equity: float) -> float:
        return max(0.0, equity * float(self.cfg["risk_per_trade"]))

    @staticmethod
    def _floor_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return math.floor(value / step + 1e-12) * step

    def calculate_volume(self, symbol: str, entry: float, sl: float, equity: float, symbol_info=None) -> float:
        if entry == sl:
            raise ValueError("Entry and stop loss cannot be equal")
        info = symbol_info or (self.client.symbol_info(symbol) if self.client else None)
        if info is None:
            raise ValueError(f"Missing symbol specification for {symbol}")
        risk = self.risk_amount(equity)
        tick_size = float(getattr(info, "trade_tick_size", 0) or getattr(info, "point", 0))
        tick_value = float(getattr(info, "trade_tick_value_loss", 0) or getattr(info, "trade_tick_value", 0))
        if tick_size <= 0 or tick_value <= 0:
            raise ValueError("Broker tick size/value unavailable; refusing unsafe fixed formula")
        stop_distance = abs(entry - sl)
        loss_per_lot = (stop_distance / tick_size) * tick_value
        if loss_per_lot <= 0:
            raise ValueError("Invalid loss per lot")
        raw = risk / loss_per_lot
        step = float(info.volume_step or 0.01)
        min_v = float(info.volume_min or step)
        max_v = float(info.volume_max or raw)
        volume = min(max_v, self._floor_step(raw, step))
        if volume < min_v:
            return 0.0
        return round(volume, 8)

    def theoretical_loss(self, entry: float, sl: float, volume: float, symbol_info) -> float:
        tick_size = float(getattr(symbol_info, "trade_tick_size", 0) or getattr(symbol_info, "point", 0))
        tick_value = float(getattr(symbol_info, "trade_tick_value_loss", 0) or getattr(symbol_info, "trade_tick_value", 0))
        return abs(entry - sl) / tick_size * tick_value * volume

    def daily_locked(self, equity: float) -> bool:
        if self.state is None:
            self.initialize_state(equity)
        draw = (self.state.day_start_equity - equity) / max(self.state.day_start_equity, 1e-9)
        return draw >= float(self.cfg["max_daily_loss"])

    def weekly_locked(self, equity: float) -> bool:
        if self.state is None:
            self.initialize_state(equity)
        draw = (self.state.week_start_equity - equity) / max(self.state.week_start_equity, 1e-9)
        return draw >= float(self.cfg["max_weekly_loss"])

    def can_trade(self, equity: float, open_risk_fraction: float = 0.0) -> tuple[bool, str]:
        if self.daily_locked(equity):
            return False, "Daily loss limit reached"
        if self.weekly_locked(equity):
            return False, "Weekly loss limit reached"
        if self.state and self.state.consecutive_losses >= int(self.cfg["max_consecutive_losses"]):
            return False, "Maximum consecutive losses reached"
        if open_risk_fraction + float(self.cfg["risk_per_trade"]) > float(self.cfg["max_total_risk"]):
            return False, "Maximum total open risk exceeded"
        return True, "OK"

    def register_closed_trade(self, profit: float) -> None:
        if self.state is None:
            return
        self.state.consecutive_losses = self.state.consecutive_losses + 1 if profit < 0 else 0
        logger.info("Risk state updated losses={}", self.state.consecutive_losses)
