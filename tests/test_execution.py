from core.execution import ExecutionEngine
from utils.types import Direction, OrderRequest, TradingMode


class Client:
    def health_check(self): return True


def test_paper_execution_has_no_mt5_order():
    e=ExecutionEngine(Client(),TradingMode.PAPER,False,"does-not-exist.flag")
    r=e.execute(OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.09,1.12,20))
    assert r.success and r.ticket


def test_live_disabled_by_default():
    e=ExecutionEngine(Client(),TradingMode.LIVE,False,"does-not-exist.flag")
    r=e.execute(OrderRequest("EURUSD",Direction.BUY,.1,1.1,1.09,1.12,20))
    assert not r.success
