from __future__ import annotations

import json
from pathlib import Path


def save_report(metrics: dict, path: str = "reports/backtest.json") -> str:
    p=Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8"); return str(p)
