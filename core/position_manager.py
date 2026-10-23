from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PositionDecision:
    new_sl: float | None = None
    partial_close_fraction: float | None = None
    close: bool = False
    reason: str = ""


class PositionManager:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def decide(self, direction: str, entry: float, current: float, sl: float, initial_risk: float, atr: float | None = None, partial_done=False) -> PositionDecision:
        if initial_risk <= 0:
            return PositionDecision()
        profit = (current - entry) if direction == "BUY" else (entry - current)
        r = profit / initial_risk
        if self.cfg.get("break_even_enabled") and r >= float(self.cfg.get("break_even_trigger_r", 1.0)):
            be = entry
            if (direction == "BUY" and be > sl) or (direction == "SELL" and be < sl):
                return PositionDecision(new_sl=be, reason="Break-even")
        if not partial_done and self.cfg.get("partial_close_enabled") and r >= float(self.cfg.get("partial_close_r", 1.5)):
            return PositionDecision(partial_close_fraction=float(self.cfg.get("partial_close_fraction", .5)), reason="Partial close")
        if self.cfg.get("trailing_enabled") and atr and r >= float(self.cfg.get("trailing_start_r", 1.5)):
            mult = float(self.cfg.get("trailing_atr_multiple", 1.0))
            candidate = current - atr * mult if direction == "BUY" else current + atr * mult
            if (direction == "BUY" and candidate > sl) or (direction == "SELL" and candidate < sl):
                return PositionDecision(new_sl=candidate, reason="ATR trailing")
        return PositionDecision()
