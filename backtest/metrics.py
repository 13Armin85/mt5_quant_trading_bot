from __future__ import annotations

import math
import numpy as np
import pandas as pd


def calculate_metrics(trades: pd.DataFrame, initial_equity: float = 10000.0) -> dict:
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    if trades.empty:
        return {"total_trades": 0, "net_profit": 0.0, "net_return": 0.0, "max_drawdown": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0}
    if "exit_time" in trades:
        trades = trades.sort_values("exit_time", kind="stable").reset_index(drop=True)
    pnl = trades["profit"].astype(float)
    if not np.isfinite(pnl).all():
        raise ValueError("Invalid trade profit")
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    equity = initial_equity + pnl.cumsum()
    peak = equity.cummax().clip(lower=initial_equity); dd = (equity - peak) / peak.replace(0, np.nan)
    returns = pnl / initial_equity
    downside = returns[returns < 0]
    std = returns.std(ddof=1); dstd = downside.std(ddof=1) if len(downside) > 1 else np.nan
    # Trade frequency is irregular: these are explicitly unannualized statistics.
    sharpe = returns.mean()/std if std and not np.isnan(std) else 0.0
    sortino = returns.mean()/dstd if dstd and not np.isnan(dstd) else 0.0
    max_dd = abs(float(dd.min())) if len(dd) else 0.0
    net = float(pnl.sum()); gross_win = float(wins.sum()); gross_loss = abs(float(losses.sum()))
    pf = gross_win/gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    expectancy = float(pnl.mean())
    monthly = pd.DataFrame({"time": pd.to_datetime(trades["exit_time"], utc=True), "profit": pnl}).set_index("time").resample("ME").profit.sum().to_dict() if "exit_time" in trades else {}
    streak_w = streak_l = max_w = max_l = 0
    for x in pnl:
        if x > 0: streak_w += 1; streak_l = 0; max_w = max(max_w, streak_w)
        elif x < 0: streak_l += 1; streak_w = 0; max_l = max(max_l, streak_l)
    calmar = (net/initial_equity)/max_dd if max_dd > 0 else 0.0
    recovery = net/(max_dd*initial_equity) if max_dd > 0 else 0.0
    return {
        "total_trades": int(len(pnl)), "winning_trades": int((pnl>0).sum()), "losing_trades": int((pnl<0).sum()),
        "win_rate": float((pnl>0).mean()), "profit_factor": pf, "expectancy": expectancy, "net_profit": net,
        "net_return": net / initial_equity, "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        "drawdown_basis": "realized equity including initial capital; intratrade drawdown unavailable",
        "ratio_basis": "unannualized per-trade returns",
        "average_win": float(wins.mean()) if len(wins) else 0.0, "average_loss": float(losses.mean()) if len(losses) else 0.0,
        "average_r": float(trades["r"].mean()) if "r" in trades else None, "max_drawdown": max_dd,
        "relative_drawdown": max_dd, "sharpe_ratio": sharpe, "sortino_ratio": sortino, "calmar_ratio": calmar,
        "recovery_factor": recovery, "maximum_consecutive_wins": max_w, "maximum_consecutive_losses": max_l,
        "average_holding_time": str((pd.to_datetime(trades.exit_time)-pd.to_datetime(trades.entry_time)).mean()) if {"entry_time","exit_time"}.issubset(trades.columns) else None,
        "exposure": float(trades.get("exposure", pd.Series([0.0])).mean()), "monthly_return": {str(k): float(v/initial_equity) for k,v in monthly.items()},
    }
