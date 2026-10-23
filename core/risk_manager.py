from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger


@dataclass(slots=True)
class RiskState:
    day_start_equity: float
    week_start_equity: float
    consecutive_losses: int = 0
    day: str = ""
    week: str = ""
    peak_equity: float = 0.0
    trades_today: int = 0


class RiskManager:
    def __init__(self, cfg: dict, client=None) -> None:
        self.cfg = cfg
        self.client = client
        self.state: RiskState | None = None

    def initialize_state(self, equity: float) -> None:
        now = datetime.now(timezone.utc)
        self.state = RiskState(equity, equity, day=now.date().isoformat(), week=now.strftime("%G-%V"), peak_equity=equity)

    def roll_state(self, equity):
        self.risk_amount(equity)
        if self.state is None:
            self.initialize_state(equity)
        now = datetime.now(timezone.utc)
        if self.state.day != now.date().isoformat():
            self.state.day = now.date().isoformat()
            self.state.day_start_equity = equity
            self.state.trades_today = 0
        if self.state.week != now.strftime("%G-%V"):
            self.state.week = now.strftime("%G-%V")
            self.state.week_start_equity = equity
        self.state.peak_equity = max(equity, self.state.peak_equity)

    def risk_amount(self, equity: float) -> float:
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Invalid account equity")
        fraction = float(self.cfg.get("risk_per_trade", .01))
        if not 0 < fraction <= .01:
            raise ValueError("Per-trade risk must be > 0 and <= 1%")
        return equity * fraction

    @staticmethod
    def _floor_step(value: float, step: float) -> float:
        if step <= 0:
            raise ValueError("Invalid volume step")
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step)))

    def loss_per_lot(self, symbol, entry, sl, info, direction=None):
        if not all(math.isfinite(v) and v > 0 for v in (entry, sl)) or entry == sl:
            raise ValueError("Invalid entry/stop")
        side = getattr(direction, "value", direction) or ("BUY" if sl < entry else "SELL")
        if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
            raise ValueError("Stop loss is on the wrong side")
        buffer = float(self.cfg.get("slippage_points", 0)) * float(info.point)
        stop = sl - buffer if side == "BUY" else sl + buffer
        if self.client is not None:
            pnl = self.client.order_calc_profit(0 if side == "BUY" else 1, symbol, 1.0, entry, stop)
            if pnl is None or not math.isfinite(pnl) or pnl >= 0:
                raise ValueError("Broker account-currency loss calculation failed")
            loss = -float(pnl)
        else:
            loss = self.theoretical_loss(entry, stop, 1, info)
        loss += float(self.cfg.get("commission_per_lot", 0))
        if not math.isfinite(loss) or loss <= 0:
            raise ValueError("Invalid estimated loss")
        return loss

    def calculate_volume(self, symbol: str, entry: float, sl: float, equity: float, symbol_info=None, open_risk: float = 0, direction=None) -> float:
        if entry == sl:
            raise ValueError("Entry and stop loss cannot be equal")
        info = symbol_info or (self.client.symbol_info(symbol) if self.client else None)
        if info is None:
            raise ValueError(f"Missing symbol specification for {symbol}")
        risk = min(self.risk_amount(equity), equity * min(.01, float(self.cfg.get("max_total_risk", .01))) - open_risk)
        if not math.isfinite(open_risk) or open_risk < 0 or risk <= 0:
            return 0.0
        loss_per_lot = self.loss_per_lot(symbol, entry, sl, info, direction)
        raw = risk / loss_per_lot
        step, min_v, max_v = float(info.volume_step), float(info.volume_min), float(info.volume_max)
        if not all(math.isfinite(v) and v > 0 for v in (step, min_v, max_v)) or min_v > max_v:
            raise ValueError("Invalid broker volume limits")
        volume = self._floor_step(min(max_v, raw), step)
        if volume < min_v:
            return 0.0
        return round(volume, 8)

    def theoretical_loss(self, entry: float, sl: float, volume: float, symbol_info) -> float:
        tick_size = float(getattr(symbol_info, "trade_tick_size", 0) or getattr(symbol_info, "point", 0))
        tick_value = float(getattr(symbol_info, "trade_tick_value_loss", 0) or getattr(symbol_info, "trade_tick_value", 0))
        if tick_size <= 0 or tick_value <= 0:
            raise ValueError("Missing broker tick size/value")
        return abs(entry - sl) / tick_size * tick_value * volume

    def account_exposure(self):
        positions, orders = self.client.positions_get(), self.client.orders_get()
        if positions is None or orders is None:
            raise ValueError("Cannot determine broker exposure")
        total = 0.0
        for item in [*positions, *orders]:
            sl = float(item.sl)
            volume = float(getattr(item, "volume", getattr(item, "volume_current", 0)))
            if not math.isfinite(sl) or sl <= 0 or not math.isfinite(volume) or volume <= 0:
                raise ValueError("Existing exposure has no valid SL/volume")
            side = "BUY" if int(item.type) in (0, 2, 4, 6) else "SELL"
            info = self.client.symbol_info(item.symbol)
            if info is None:
                raise ValueError("Existing symbol specification unavailable")
            # Positions use current liquidation price; pending orders use entry.
            entry = float(getattr(item, "price_current", item.price_open)) if hasattr(item, "volume") else float(item.price_open)
            if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
                raise ValueError("Existing stop has crossed the market")
            total += self.loss_per_lot(item.symbol, entry, sl, info, side) * volume
        return total, list(positions), list(orders)

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
        self.risk_amount(equity)
        self.roll_state(equity)
        if (self.state.peak_equity - equity) / self.state.peak_equity >= float(self.cfg.get("max_drawdown", .10)):
            return False, "Maximum drawdown reached"
        if self.state.trades_today >= int(self.cfg.get("max_trades_per_day", 10)):
            return False, "Daily trade count reached"
        if self.daily_locked(equity):
            return False, "Daily loss limit reached"
        if self.weekly_locked(equity):
            return False, "Weekly loss limit reached"
        if self.state and self.state.consecutive_losses >= int(self.cfg["max_consecutive_losses"]):
            return False, "Maximum consecutive losses reached"
        if not math.isfinite(open_risk_fraction) or open_risk_fraction >= min(.01, float(self.cfg["max_total_risk"])):
            return False, "Maximum total open risk exceeded"
        return True, "OK"

    def register_closed_trade(self, profit: float) -> None:
        if self.state is None:
            return
        self.state.consecutive_losses = self.state.consecutive_losses + 1 if profit < 0 else 0
        logger.info("Risk state updated losses={}", self.state.consecutive_losses)
