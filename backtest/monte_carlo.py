from __future__ import annotations

import numpy as np


def simulate(trade_returns, simulations: int = 1000, seed: int = 42) -> dict:
    r = np.asarray(list(trade_returns), dtype=float)
    if r.size == 0: return {"simulations": 0}
    rng = np.random.default_rng(seed); final=[]; drawdowns=[]
    for _ in range(simulations):
        sample = rng.choice(r, size=len(r), replace=True); curve=np.cumprod(1+sample); peak=np.maximum.accumulate(curve); dd=(curve-peak)/peak
        final.append(curve[-1]-1); drawdowns.append(abs(dd.min()))
    a=np.asarray(final); d=np.asarray(drawdowns)
    return {"simulations":simulations,"median_return":float(np.median(a)),"p05_return":float(np.quantile(a,.05)),"p95_return":float(np.quantile(a,.95)),
            "median_max_drawdown":float(np.median(d)),"p95_max_drawdown":float(np.quantile(d,.95)),"probability_drawdown_gt_30pct":float((d>.30).mean()),"risk_of_ruin":float((a<=-1).mean())}
