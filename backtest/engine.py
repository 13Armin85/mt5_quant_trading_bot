from __future__ import annotations

import pandas as pd

from backtest.metrics import calculate_metrics


class BacktestEngine:
    """Bar-based SL/TP backtester with explicit spread/commission/slippage assumptions."""
    def __init__(self, initial_equity: float = 10000, spread: float = 0.0, commission_per_lot: float = 0.0, slippage: float = 0.0) -> None:
        self.initial_equity = initial_equity; self.spread = spread; self.commission_per_lot = commission_per_lot; self.slippage = slippage

    def run(self, bars: pd.DataFrame, signals: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        trades = []
        for s in signals.itertuples():
            if s.direction not in ("BUY", "SELL"): continue
            future = bars[bars.time > s.time]
            if future.empty: continue
            entry = float(s.entry) + (self.spread/2 + self.slippage) * (1 if s.direction == "BUY" else -1)
            exit_price = None; exit_time = None; outcome = None
            for b in future.itertuples():
                if s.direction == "BUY":
                    hit_sl, hit_tp = b.low <= s.sl, b.high >= s.tp
                else:
                    hit_sl, hit_tp = b.high >= s.sl, b.low <= s.tp
                if hit_sl and hit_tp:
                    hit_tp = False  # conservative same-bar assumption
                if hit_sl: exit_price=float(s.sl); exit_time=b.time; outcome="SL"; break
                if hit_tp: exit_price=float(s.tp); exit_time=b.time; outcome="TP"; break
            if exit_price is None: continue
            signed = (exit_price-entry) if s.direction == "BUY" else (entry-exit_price)
            profit = signed*float(s.contract_value_per_price)*float(s.volume) - self.commission_per_lot*float(s.volume)
            risk = abs(entry-float(s.sl))*float(s.contract_value_per_price)*float(s.volume)
            trades.append({"entry_time":s.time,"exit_time":exit_time,"direction":s.direction,"entry":entry,"exit":exit_price,"profit":profit,"r":profit/risk if risk else 0,"outcome":outcome,"exposure":1.0})
        df = pd.DataFrame(trades)
        return df, calculate_metrics(df, self.initial_equity)
