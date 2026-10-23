from __future__ import annotations

import pandas as pd
import math
from core.data_feed import DataFeed
from core.risk_manager import RiskManager

from backtest.metrics import calculate_metrics


class BacktestEngine:
    """Bar-based SL/TP backtester with explicit spread/commission/slippage assumptions."""
    def __init__(self, initial_equity: float = 10000, spread: float = 0.0, commission_per_lot: float = 0.0, slippage: float = 0.0, risk_fraction=.01, volume_step=.01, volume_min=.01, volume_max=100, tick_size=None, minimum_net_rr=0) -> None:
        self.initial_equity = initial_equity; self.spread = spread; self.commission_per_lot = commission_per_lot; self.slippage = slippage
        self.risk_fraction, self.volume_step, self.volume_min, self.volume_max, self.tick_size = risk_fraction, volume_step, volume_min, volume_max, tick_size
        self.minimum_net_rr = minimum_net_rr
        if not math.isfinite(minimum_net_rr) or minimum_net_rr<0:
            raise ValueError("Invalid minimum_net_rr")
        if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
            raise ValueError("Invalid tick_size")
        if not all(math.isfinite(v) for v in (initial_equity, spread, commission_per_lot, slippage, risk_fraction, volume_step, volume_min, volume_max)) or initial_equity <= 0 or min(spread, commission_per_lot, slippage) < 0 or not 0 < risk_fraction <= .01 or min(volume_step, volume_min) <= 0 or volume_max < volume_min:
            raise ValueError("Invalid backtest capital, costs or broker/risk limits")
        self.rejections = []

    def run(self, bars: pd.DataFrame, signals: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        bars, signals = bars.copy(), signals.copy()
        for frame in (bars, signals):
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
            if not frame.time.is_monotonic_increasing or frame.time.duplicated().any():
                raise ValueError("Backtest times must be unique and ordered")
        DataFeed.validate_bars(bars)
        trades = []
        self.rejections = []
        seen = set()
        for s in signals.itertuples():
            if s.direction not in ("BUY", "SELL"): continue
            key = getattr(s, "signal_id", str(s.time))
            if key in seen:
                continue
            seen.add(key)
            future = bars[bars.time > s.time]
            if future.empty: continue
            if hasattr(s, "news_available_at") and pd.to_datetime(s.news_available_at, utc=True) > s.time:
                raise ValueError("Future news snapshot in backtest signal")
            buy = s.direction == "BUY"
            # Never fill at a historical signal close: execute at the next bar open.
            # MT5 forex OHLC is normally Bid. Ask is synthesized using explicit spread.
            entry = float(future.iloc[0].open) + (self.spread + self.slippage if buy else -self.slippage)
            sl, tp, value = float(s.sl), float(s.tp), float(s.contract_value_per_price)
            if not all(math.isfinite(v) and v > 0 for v in (entry, sl, tp, value)) or not (sl < entry < tp if buy else tp < entry < sl):
                self.rejections.append({"time": str(s.time), "reason": "Invalid entry/SL/TP/contract value"})
                continue
            if self.tick_size and any(abs(v / self.tick_size - round(v / self.tick_size)) > 1e-6 for v in (sl, tp)):
                self.rejections.append({"time": str(s.time), "reason": "Stop/target off broker tick grid"})
                continue
            entry_time = future.iloc[0].time
            realized = sum(t["profit"] for t in trades if t["exit_time"] < entry_time)
            equity = self.initial_equity + realized
            exposure = sum(t["risk"] for t in trades if t["exit_time"] >= entry_time)
            budget = max(0, equity * self.risk_fraction - exposure)
            loss_per_lot = (abs(entry - sl) + self.slippage) * value + self.commission_per_lot
            net_reward = (abs(tp-entry)-self.slippage)*value-self.commission_per_lot
            signal_minimum=float(getattr(s,"minimum_net_rr",0))
            minimum_rr = max(self.minimum_net_rr,signal_minimum)
            if not math.isfinite(signal_minimum) or signal_minimum<0 or net_reward/loss_per_lot+1e-8<minimum_rr:
                self.rejections.append({"time":str(s.time),"reason":"Net reward/risk below minimum after next-bar fill and costs"})
                continue
            raw = min(budget / loss_per_lot, self.volume_max, float(getattr(s, "volume", self.volume_max)))
            volume = RiskManager._floor_step(raw, self.volume_step)
            if not math.isfinite(volume) or volume < self.volume_min:
                self.rejections.append({"time": str(s.time), "reason": "Insufficient aggregate risk budget/minimum volume"})
                continue
            exit_price = None; exit_time = None; outcome = None
            for b in future.itertuples():
                if buy:
                    hit_sl, hit_tp = b.low <= sl, b.high >= tp
                else:
                    hit_sl, hit_tp = b.high + self.spread >= sl, b.low + self.spread <= tp
                if hit_sl and hit_tp:
                    hit_tp = False  # conservative same-bar assumption
                if hit_sl:
                    opened = float(b.open) if buy else float(b.open) + self.spread
                    exit_price = min(sl, opened) - self.slippage if buy else max(sl, opened) + self.slippage
                    exit_time=b.time; outcome="SL"; break
                if hit_tp:
                    exit_price = tp - self.slippage if buy else tp + self.slippage
                    exit_time=b.time; outcome="TP"; break
            if exit_price is None:
                last = future.iloc[-1]
                exit_price = float(last.close) - self.slippage if buy else float(last.close) + self.spread + self.slippage
                exit_time, outcome = last.time, "END_OF_DATA"
            signed = (exit_price-entry) if buy else (entry-exit_price)
            profit = signed * value * volume - self.commission_per_lot * volume
            risk = loss_per_lot * volume
            trades.append({"signal_time":s.time,"entry_time":entry_time,"exit_time":exit_time,"direction":s.direction,"entry":entry,"exit":exit_price,"volume":volume,"risk":risk,"profit":profit,"r":profit/risk if risk else 0,"outcome":outcome})
        df = pd.DataFrame(trades)
        if not df.empty:
            df = df.sort_values("exit_time", kind="stable").reset_index(drop=True)
        return df, calculate_metrics(df, self.initial_equity)
