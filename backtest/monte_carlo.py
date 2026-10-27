from __future__ import annotations

import numpy as np


def simulate(trade_returns, simulations: int = 1000, seed: int = 42, block_size: int = 5) -> dict:
    r = np.asarray(list(trade_returns), dtype=float)
    if r.size == 0: return {"simulations": 0}
    if not np.isfinite(r).all() or simulations<1 or isinstance(block_size,bool) or int(block_size)!=block_size or block_size<1:
        raise ValueError("Invalid block bootstrap settings/returns")
    if len(r)<max(20,block_size*2):
        return {"simulations":0,"status":"Insufficient trades for block uncertainty estimate","trades":len(r),"block_size":block_size}
    rng = np.random.default_rng(seed); final=[]; drawdowns=[]
    for _ in range(simulations):
        starts=rng.integers(0,len(r),size=int(np.ceil(len(r)/block_size)))
        sample=np.concatenate([r[(np.arange(block_size)+start)%len(r)] for start in starts])[:len(r)]
        curve=np.concatenate([[1.],np.cumprod(np.maximum(0,1+sample))]); peak=np.maximum.accumulate(curve); dd=(curve-peak)/peak
        final.append(curve[-1]-1); drawdowns.append(abs(dd.min()))
    a=np.asarray(final); d=np.asarray(drawdowns)
    return {"simulations":simulations,"seed":seed,"block_size":block_size,"basis":"Circular trade blocks; not calendar-time annualization; block size is a research assumption",
            "median_return":float(np.median(a)),"p05_return":float(np.quantile(a,.05)),"p95_return":float(np.quantile(a,.95)),
            "median_max_drawdown":float(np.median(d)),"p95_max_drawdown":float(np.quantile(d,.95)),"probability_drawdown_gt_30pct":float((d>.30).mean()),"risk_of_ruin":float((a<=-1).mean())}
