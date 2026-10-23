from signals.confidence_engine import ConfidenceEngine
from signals.filters import signal_id, spread_is_valid


def test_confidence_weighted():
    c=ConfidenceEngine({"trend":.5,"structure":.5})
    assert c.score({"trend":.8,"structure":.8},"BUY")==.8
    assert abs(c.score({"trend":.8,"structure":.8},"SELL")-.2)<1e-9


def test_signal_id_deterministic():
    a=signal_id("EURUSD","M5","BUY","2026-01-01","trend")
    b=signal_id("EURUSD","M5","BUY","2026-01-01","trend")
    assert a==b


def test_spread_filter():
    ok,_=spread_is_valid(2,[1,1.2,1.5,1.6,1.8],2)
    assert ok
    ok,_=spread_is_valid(10,[1,1.2,1.5,1.6,1.8],2)
    assert not ok
