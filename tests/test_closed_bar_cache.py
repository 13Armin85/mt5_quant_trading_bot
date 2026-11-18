from datetime import datetime, timezone, timedelta

import pytest

from core.data_feed import DataFeed


def feed_fixture(monkeypatch):
    now = [datetime(2026,11,18,10,0,5,tzinfo=timezone.utc)]
    clock = [0.]
    monkeypatch.setattr('core.data_feed.time.monotonic',lambda:clock[0])
    class Client:
        def __init__(self):
            self.calls=[]
            self.rows=[{'time':int((now[0].replace(second=0)-timedelta(minutes=n)).timestamp()),
                'open':1.,'high':2.,'low':.5,'close':1.5,'tick_volume':5} for n in (3,2,1)]
            self.available=True
        def timeframe(self,tf):return tf
        def copy_rates_from_pos(self,symbol,tf,start,count):
            assert start==1
            self.calls.append(count)
            return [dict(r) for r in self.rows[-count:]] if self.available else None
    client=Client()
    return DataFeed(client,clock=lambda:now[0]),client,clock,now


def test_unchanged_closed_bar_uses_small_probe_and_returns_independent_frame(monkeypatch):
    feed,client,_,_=feed_fixture(monkeypatch)
    first=feed.bars('EURUSD','M1',3)
    first.loc[0,'close']=999
    second=feed.bars('EURUSD','M1',3)
    assert client.calls==[3,1]
    assert second.close.iloc[0]==1.5


def test_bar_correction_and_periodic_refresh_reload_history(monkeypatch):
    feed,client,clock,_=feed_fixture(monkeypatch)
    feed.bars('EURUSD','M1',3)
    client.rows[-1]['high']=3.
    assert feed.bars('EURUSD','M1',3).high.iloc[-1]==3
    assert client.calls==[3,1,3]
    client.rows[0]['high']=4.
    clock[0]=31.
    assert feed.bars('EURUSD','M1',3).high.iloc[0]==4
    assert client.calls[-1]==3


def test_cached_bar_still_checks_freshness_and_never_masks_provider_failure(monkeypatch):
    feed,client,_,now=feed_fixture(monkeypatch)
    feed.bars('EURUSD','M1',3)
    now[0]+=timedelta(minutes=2)
    with pytest.raises(ValueError,match='stale'):
        feed.bars('EURUSD','M1',3)
    client.available=False
    assert feed.bars('EURUSD','M1',3).empty
