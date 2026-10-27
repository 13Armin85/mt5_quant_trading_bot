from __future__ import annotations

import hashlib
import math
from statistics import median
from datetime import datetime


def signal_id(symbol: str, timeframe: str, direction: str, candle_time: datetime | str, strategy: str) -> str:
    raw = f"{symbol}|{timeframe}|{direction}|{candle_time}|{strategy}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def assess_spread(current, historical=None, multiplier=2., absolute_limit=None, live_samples=None, minimum_live_samples=20):
    """All values are broker points. A zero median is not a usable ratio baseline."""
    result = {"points":current,"allowed":False,"score":0.,"basis":"invalid",
              "baseline_points":None,"limit_points":absolute_limit}
    if not math.isfinite(float(multiplier)) or multiplier<=0:
        raise ValueError("Spread multiplier must be finite and positive")
    if absolute_limit is not None and (not math.isfinite(float(absolute_limit)) or absolute_limit<=0):
        raise ValueError("Absolute spread limit must be finite and positive")
    if current is None or not math.isfinite(float(current)) or current<0:
        result["points"]=None
        result["reason"]="Invalid current spread"
        return result
    valid = lambda values:[float(v) for v in ([] if values is None else values) if v is not None and math.isfinite(float(v)) and v>=0]
    bars, ticks = valid(historical),valid(live_samples)
    result.update(historical_samples=len(bars),zero_historical_samples=bars.count(0),live_samples=len(ticks))
    baseline,basis = None,"absolute_cap"
    if len(ticks)>=minimum_live_samples and median(ticks)>0:
        baseline,basis = median(ticks),"recent_ticks"
    elif bars and median(bars)>0:
        baseline,basis = median(bars),"historical_bars"
    limit = baseline*multiplier if baseline is not None else absolute_limit
    if limit is None:
        result.update(basis="unavailable",reason="No positive spread baseline or absolute limit")
        return result
    if absolute_limit is not None:
        limit=min(limit,absolute_limit)
    tolerance=max(1e-8,abs(limit)*1e-9)
    allowed=current<=limit+tolerance
    result.update(allowed=allowed,basis=basis,baseline_points=baseline,limit_points=limit,
        score=max(0.,min(1.,1-current/max(limit*1.5,1e-9))),
        reason="Spread within limit" if allowed else "Spread exceeds limit")
    return result


def spread_is_valid(current, historical, multiplier=2., absolute_limit=None):
    status=assess_spread(current,historical,multiplier,absolute_limit)
    return status["allowed"],status["score"]
