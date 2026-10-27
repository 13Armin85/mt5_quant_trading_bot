"""Reproducible research metadata without credentials or account identifiers."""
import hashlib
import platform
from importlib.metadata import version
from pathlib import Path
import pandas as pd

from monitoring.decisions import config_revision


def manifest(bars, cfg, costs):
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("analysis","signals","core","backtest","ml","database","monitoring","news","scripts"):
        for path in sorted((root/folder).glob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode());digest.update(path.read_bytes())
    for name in ("main.py","config.py"):
        digest.update(name.encode());digest.update((root/name).read_bytes())
    return {"source_hash":digest.hexdigest(),"config_revision":config_revision(cfg),
        "data_hash":hashlib.sha256(pd.util.hash_pandas_object(bars,index=True).values.tobytes()).hexdigest(),
        "bars":len(bars),"start":str(bars.time.min()),"end":str(bars.time.max()),
        "data_quality":{"unique_times":not bars.time.duplicated().any(),"ordered":bars.time.is_monotonic_increasing,
            "large_time_gaps":int((bars.time.diff()>bars.time.diff().median()*2).sum()),
            "gap_note":"Trading sessions/holidays must be checked before treating gaps as missing data"},
        "python":platform.python_version(),"pandas":pd.__version__,"costs":costs,
        "packages":{name:version(name) for name in ("numpy","scikit-learn","SQLAlchemy")},
        "cost_status":"exploratory_unverified","historical_news":False,"live_ready":False,
        "acceptance":{"minimum_validation_trades":cfg.get("validation.minimum_trades",200),
            "minimum_profit_factor":cfg.get("validation.minimum_profit_factor",1.3),
            "maximum_drawdown":cfg.get("validation.maximum_drawdown",.25),
            "require_positive_expectancy":True,"test_used_for_selection":False}}
