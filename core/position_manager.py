from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from analysis.market_structure import analyze_structure


@dataclass(slots=True)
class PositionDecision:
    new_sl: float | None = None
    partial_close_fraction: float | None = None
    close: bool = False
    reason: str = ""


class PositionManager:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    def invalidation_reason(self, direction, frames, opened_at):
        """Require independent context and consecutive completed post-entry bars.

        Losing an entry trigger alone (or an entry filter returning HOLD) is not
        evidence that an existing position's thesis has failed.
        """
        if not self.cfg.get("invalidation_exit_enabled") or not opened_at:
            return None
        entry, setup, trend = (frames.get(role) for role in ("entry", "setup", "trend"))
        if any(frame is None or frame.empty for frame in (entry, setup, trend)):
            return None
        count = int(self.cfg.get("invalidation_confirmation_bars", 2))
        rows = entry.tail(count)
        required = ["close", "ema_20", "rsi", "macd_hist"]
        if len(rows) < count or not set(["time", *required]).issubset(rows.columns):
            return None
        if not all(math.isfinite(float(v)) for v in rows[required].to_numpy().flat):
            return None
        opened = pd.Timestamp(opened_at)
        if opened.tzinfo is None:
            opened = opened.tz_localize("UTC")
        if not (pd.to_datetime(rows.time, utc=True) >= opened).all():
            return None
        buy = direction == "BUY"
        momentum = ((rows.close < rows.ema_20) & (rows.rsi < 45) & (rows.macd_hist < 0)
                    if buy else (rows.close > rows.ema_20) & (rows.rsi > 55) & (rows.macd_hist > 0))
        if not momentum.all():
            return None
        context = trend.iloc[-1]
        emas = [float(context.get(name, float("nan"))) for name in ("ema_20", "ema_50", "ema_200")]
        opposing_trend = all(math.isfinite(v) for v in emas) and (
            emas[0] < emas[1] < emas[2] if buy else emas[0] > emas[1] > emas[2])
        opposing_structure = False
        if {"high", "low", "close"}.issubset(setup.columns):
            structure = analyze_structure(setup)
            opposing_structure = structure["direction"] == ("BEARISH" if buy else "BULLISH")
        if opposing_trend or opposing_structure:
            context_reason = "opposing confirmed structure" if opposing_structure else "opposing higher-timeframe trend"
            return f"Analysis invalidated: {context_reason} and {count} closed bars of opposing momentum"
        return None

    def decide(self, direction: str, entry: float, current: float, sl: float, initial_risk: float, atr: float | None = None, partial_done=False, *, invalidation_reason=None) -> PositionDecision:
        if not all(math.isfinite(v) and v > 0 for v in (entry, current, initial_risk)):
            return PositionDecision()
        profit = (current - entry) if direction == "BUY" else (entry - current)
        r = profit / initial_risk
        if self.cfg.get("loss_cut_enabled") and r <= -float(self.cfg.get("max_adverse_r", .75)):
            return PositionDecision(close=True, reason="Maximum adverse excursion reached")
        if invalidation_reason:
            return PositionDecision(close=True, reason=invalidation_reason)
        if self.cfg.get("break_even_enabled") and r >= float(self.cfg.get("break_even_trigger_r", 1.0)):
            be = entry
            if (direction == "BUY" and be > sl) or (direction == "SELL" and be < sl):
                return PositionDecision(new_sl=be, reason="Break-even")
        if not partial_done and self.cfg.get("partial_close_enabled") and r >= float(self.cfg.get("partial_close_r", 1.5)):
            return PositionDecision(partial_close_fraction=float(self.cfg.get("partial_close_fraction", .5)), reason="Partial close")
        if self.cfg.get("trailing_enabled") and atr and math.isfinite(atr) and atr > 0 and r >= float(self.cfg.get("trailing_start_r", 1.5)):
            mult = float(self.cfg.get("trailing_atr_multiple", 1.0))
            candidate = current - atr * mult if direction == "BUY" else current + atr * mult
            if (direction == "BUY" and candidate > sl) or (direction == "SELL" and candidate < sl):
                return PositionDecision(new_sl=candidate, reason="ATR trailing")
        return PositionDecision()
