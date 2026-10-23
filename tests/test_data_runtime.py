from types import SimpleNamespace
from datetime import datetime, timezone
import pandas as pd
import pytest

from core.data_feed import DataFeed
from core.runtime_lock import RuntimeLock
from core.symbol_resolver import SymbolResolver
from database.repository import Repository
from config import EnvSettings, load_config


def test_only_closed_bars_requested():
    now = pd.Timestamp.now(tz="UTC").floor("5min") - pd.Timedelta(minutes=5)
    class Client:
        def timeframe(self, tf): return 5
        def copy_rates_from_pos(self, symbol, tf, start, count):
            assert start == 1
            return [{"time":int(now.timestamp()),"open":1,"high":2,"low":.5,"close":1,"tick_volume":5}]
    assert len(DataFeed(Client()).bars("EURUSD","M5",1))==1


def test_duplicate_bars_rejected():
    frame=pd.DataFrame({"time":[datetime.now(timezone.utc)]*2,"open":[1,1],"high":[2,2],"low":[.5,.5],"close":[1,1]})
    with pytest.raises(ValueError,match="Duplicate"):
        DataFeed.validate_bars(frame)


def test_ambiguous_symbol_requires_explicit_mapping(broker):
    resolver=SymbolResolver(broker,{"symbols":{"EURUSD":{"aliases":["EURUSD","EURUSDm"]}}})
    with pytest.raises(ValueError,match="Ambiguous"):
        resolver.resolve("EURUSD")
    resolver.symbol_config["symbols"]["EURUSD"]["broker_symbol"]="EURUSDm"
    assert resolver.resolve("EURUSD")=="EURUSDm"


def test_catalog_reports_litefinance_suffix_without_guessing(broker, monkeypatch):
    calls=[]
    def catalog():
        calls.append(1)
        return [SimpleNamespace(name="EURUSD_o"),SimpleNamespace(name="XAUUSD_o")]
    monkeypatch.setattr(broker,"symbols_get",catalog,raising=False)
    monkeypatch.setattr(broker,"symbol_info",lambda *a:pytest.fail("Missing symbols must not be probed"))
    resolver=SymbolResolver(broker,{"symbols":{"EURUSD":{"aliases":["EURUSD","EURUSDm"]}}})
    for _ in range(2):
        with pytest.raises(ValueError,match="EURUSD_o"):
            resolver.resolve("EURUSD")
    assert len(calls)==1


def test_exact_litefinance_mapping_is_preserved(broker, monkeypatch):
    selected=[]
    monkeypatch.setattr(broker,"symbols_get",lambda:[SimpleNamespace(name="EURUSD_o"),SimpleNamespace(name="EURUSD_other")],raising=False)
    monkeypatch.setattr(broker,"ensure_symbol",lambda name:selected.append(name) or True)
    resolver=SymbolResolver(broker,{"symbols":{"EURUSD":{"broker_symbol":"EURUSD_o","aliases":["EURUSD"]}}})
    assert resolver.resolve("EURUSD")=="EURUSD_o" and selected==["EURUSD_o"]


def test_catalog_ambiguity_does_not_choose_first(broker, monkeypatch):
    monkeypatch.setattr(broker,"symbols_get",lambda:[SimpleNamespace(name="EURUSD_o"),SimpleNamespace(name="EURUSD_raw")],raising=False)
    resolver=SymbolResolver(broker,{"symbols":{"EURUSD":{"aliases":["EURUSD_o","EURUSD_raw"]}}})
    with pytest.raises(ValueError,match="Ambiguous"):
        resolver.resolve("EURUSD")


def test_failed_catalog_does_not_fall_back_to_guessing(broker, monkeypatch):
    monkeypatch.setattr(broker,"symbols_get",lambda:None,raising=False)
    with pytest.raises(ValueError,match="catalog unavailable"):
        SymbolResolver(broker,{}).resolve("EURUSD")


def test_single_instance_lock(tmp_path):
    with RuntimeLock(tmp_path/"bot.lock"):
        with pytest.raises(RuntimeError,match="Another"):
            with RuntimeLock(tmp_path/"bot.lock"): pass
    with RuntimeLock(tmp_path/"bot.lock"): pass


def test_atomic_execution_claim_and_state_restart(tmp_path):
    url="sqlite:///"+str(tmp_path/"journal.db")
    repo=Repository(url)
    assert repo.claim_execution("account:signal",{"created":"2026-01-01T00:00:00+00:00"})
    repo.set_state("risk",{"day_start_equity":10000})
    restarted=Repository(url)
    assert not restarted.claim_execution("account:signal",{})
    assert restarted.get_state("risk")["day_start_equity"]==10000
    assert restarted.attempts("account")[0].status=="SUBMITTING"


def test_empty_env_template_login():
    assert EnvSettings(_env_file=None,MT5_LOGIN="").mt5_login is None


def test_runtime_rejects_risk_above_one_percent():
    cfg=load_config().model_copy(deep=True)
    cfg.settings["trading"]["risk_per_trade"]=.02
    with pytest.raises(ValueError,match="0.01"):
        cfg.validate_runtime()
