from __future__ import annotations

import json
import math
from pathlib import Path


def save_report(metrics: dict, path: str = "reports/backtest.json") -> str:
    def clean(value):
        if isinstance(value, dict):
            return {str(k):clean(v) for k,v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    p=Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean(metrics), indent=2, default=str, allow_nan=False), encoding="utf-8")
    return str(p)
