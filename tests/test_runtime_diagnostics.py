from zoneinfo import ZoneInfo

import pandas as pd

from config import load_config
from core.data_feed import DataFeed
from monitoring.diagnostics import inspect_runtime


def test_doctor_reports_clocks_bar_availability_and_spread_without_execution(broker,monkeypatch,tmp_path):
    cfg=load_config().model_copy(deep=True)
    cfg.env.database_url="sqlite:///"+str(tmp_path/"not_created.db")
    broker.market_timezone=ZoneInfo("Europe/Helsinki")
    def forbidden(*args,**kwargs):
        raise AssertionError("Read-only diagnostics must never submit or construct execution")
    monkeypatch.setattr(broker,"order_send",forbidden)
    monkeypatch.setattr("core.execution.ExecutionEngine.__init__",forbidden)
    def bars(*args,**kwargs):
        df=pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")-pd.Timedelta(minutes=5)],"spread":[0]})
        df.attrs={"last_bar_closed_at":pd.Timestamp.now(tz="UTC").isoformat(),"freshness_basis":"wall_clock"}
        return df
    monkeypatch.setattr(DataFeed,"bars",bars)
    report=inspect_runtime(cfg,broker,["EURUSD"])
    assert report["read_only"] and not report["orders_enabled"]
    assert report["clocks"]["tehran"].endswith("+03:30")
    assert report["symbols"]["EURUSD"]["spread"]["allowed"]
    assert all(bar["valid"] for bar in report["symbols"]["EURUSD"]["bars"].values())
    assert not (tmp_path/"not_created.db").exists()
