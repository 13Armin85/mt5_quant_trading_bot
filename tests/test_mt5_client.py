from types import SimpleNamespace
import pytest
import core.mt5_client as module
from core.mt5_client import MT5Client, MT5Error
from config import EnvSettings
from datetime import datetime, timezone
import numpy as np


def fake_mt5():
    account=SimpleNamespace(login=123,server="Demo")
    terminal=SimpleNamespace(connected=True)
    return SimpleNamespace(initialize=lambda **kw:True,shutdown=lambda:None,
        account_info=lambda:account,terminal_info=lambda:terminal,last_error=lambda:(-1,"not logged"))


def test_initialize_passes_credentials_without_logging(monkeypatch):
    fake=fake_mt5(); calls=[]
    fake.initialize=lambda **kwargs:calls.append(kwargs) or True
    monkeypatch.setattr(module,"mt5",fake)
    client=MT5Client(EnvSettings(_env_file=None,MT5_LOGIN=123,MT5_SERVER="Demo",MT5_PASSWORD="test-only",MT5_PATH="terminal.exe"))
    assert client.initialize()
    assert calls[0]=={"login":123,"server":"Demo","password":"test-only","path":"terminal.exe"}


def test_wrong_account_is_rejected(monkeypatch):
    monkeypatch.setattr(module,"mt5",fake_mt5())
    client=MT5Client(EnvSettings(_env_file=None,MT5_LOGIN=456))
    with pytest.raises(MT5Error,match="differs"):
        client.initialize()
    assert not client.connected


def test_account_switch_invalidates_connection(monkeypatch):
    fake=fake_mt5(); monkeypatch.setattr(module,"mt5",fake)
    client=MT5Client(EnvSettings(_env_file=None)); client.initialize()
    fake.account_info=lambda:SimpleNamespace(login=456,server="Real")
    assert not client.health_check()
    with pytest.raises(MT5Error): client.initialize()


def test_reconnect_has_bounded_attempts(monkeypatch):
    fake=fake_mt5();calls=[]; waits=[]
    fake.initialize=lambda **kw:calls.append(1) or False
    monkeypatch.setattr(module,"mt5",fake)
    monkeypatch.setattr(module.time,"sleep",lambda delay:waits.append(delay))
    with pytest.raises(MT5Error,match="exhausted"):
        MT5Client(EnvSettings(_env_file=None)).reconnect(3,5)
    assert len(calls)==3 and waits==[5,5]


@pytest.mark.parametrize("month,offset", [(1,2),(7,3),(10,3),(12,2)])
def test_litefinance_market_time_normalizes_with_dst(month,offset):
    client=MT5Client(EnvSettings(_env_file=None),"Europe/Helsinki")
    raw=datetime(2026,month,10,12,tzinfo=timezone.utc)
    expected=datetime(2026,month,10,12-offset,tzinfo=timezone.utc)
    assert client.market_timestamp_to_utc(raw.timestamp())==expected.timestamp()
    assert client.utc_to_market_time(expected)==raw


def test_litefinance_bar_times_are_copied_and_normalized():
    client=MT5Client(EnvSettings(_env_file=None),"Europe/Helsinki")
    raw=int(datetime(2026,10,23,23,55,tzinfo=timezone.utc).timestamp())
    rates=np.array([(raw,1.0)],dtype=[("time","i8"),("close","f8")])
    normalized=client.normalize_market_times(rates)
    assert normalized[0]["time"]==raw-10800
    assert rates[0]["time"]==raw


def test_litefinance_tick_normalization_preserves_price_and_milliseconds(monkeypatch):
    fake=fake_mt5()
    raw=int(datetime(2026,10,23,23,59,58,tzinfo=timezone.utc).timestamp())
    class Tick:
        # Real MT5 structs expose _asdict, but not namedtuple._replace.
        def __init__(self,time,time_msc,bid,ask):
            self.time,self.time_msc,self.bid,self.ask=time,time_msc,bid,ask
        def _asdict(self):
            return vars(self).copy()
    fake.symbol_info_tick=lambda symbol:Tick(raw,raw*1000+147,1.1,1.1002)
    monkeypatch.setattr(module,"mt5",fake)
    client=MT5Client(EnvSettings(_env_file=None),"Europe/Helsinki");client.initialize()
    tick=client.symbol_info_tick("EURUSD_o")
    assert tick.time==raw-10800 and tick.time_msc==(raw-10800)*1000+147
    assert tick.bid==1.1 and tick.ask==1.1002
