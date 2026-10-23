from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from types import SimpleNamespace
from datetime import datetime, timezone


@pytest.fixture(autouse=True)
def prohibit_real_mt5(monkeypatch):
    import core.mt5_client as module
    if module.mt5 is not None:
        def forbidden(*args, **kwargs):
            raise AssertionError("Real MT5 access is forbidden in automated tests")
        monkeypatch.setattr(module.mt5, "initialize", forbidden)
        monkeypatch.setattr(module.mt5, "order_send", forbidden)


@pytest.fixture
def broker():
    class Broker:
        def __init__(self):
            self.info = SimpleNamespace(point=.00001, trade_tick_size=.00001, trade_tick_value_loss=1, trade_tick_value=1,
                volume_min=.01, volume_max=100, volume_step=.01, digits=5, trade_stops_level=10,
                trade_freeze_level=0, filling_mode=3, trade_exemode=2, trade_mode=4)
            self.account = SimpleNamespace(equity=10000, margin_free=10000, trade_allowed=True, trade_expert=True, margin_mode=2)
            self.quote = SimpleNamespace(bid=1.0998, ask=1.1, time=datetime.now(timezone.utc).timestamp())
            self.positions, self.orders, self.deals, self.history = [], [], [], []
            self.sent, self.code, self.check_code, self.margin = [], 10009, 0, 100
            self.missing_sl = False
        def health_check(self): return True
        def symbol_info(self, symbol): return self.info
        def ensure_symbol(self, symbol): return True
        def symbol_info_tick(self, symbol): return self.quote
        def account_info(self): return self.account
        def terminal_info(self): return SimpleNamespace(trade_allowed=True, tradeapi_disabled=False)
        def positions_get(self, **kwargs): return self.positions
        def orders_get(self, **kwargs): return self.orders
        def history_deals_get(self, *args, **kwargs): return self.deals
        def history_orders_get(self, *args, **kwargs): return self.history
        def order_calc_profit(self, side, symbol, volume, entry, exit): return (exit-entry) * 100000 * volume * (1 if side == 0 else -1)
        def order_calc_margin(self, *args): return self.margin
        def order_check(self, req): return SimpleNamespace(retcode=self.check_code)
        def order_send(self, req):
            self.sent.append(req.copy())
            if "position" in req:
                self.positions = []
                return SimpleNamespace(retcode=10009)
            if self.code is None:
                return None
            volume = req["volume"] / 2 if self.code == 10010 else req["volume"]
            if self.code in (10009, 10010):
                self.positions = [SimpleNamespace(ticket=123, identifier=123, symbol=req["symbol"], type=req["type"], magic=req["magic"], comment=req["comment"],
                    volume=volume, price_open=req["price"], price_current=req["price"], sl=0 if self.missing_sl else req["sl"], tp=req["tp"])]
            return SimpleNamespace(retcode=self.code, order=123, deal=456, volume=volume, price=req["price"])
    return Broker()


@pytest.fixture
def risk_cfg():
    return {"risk_per_trade":.01,"max_total_risk":.01,"max_daily_loss":.03,"max_weekly_loss":.06,"max_consecutive_losses":3,
            "commission_per_lot":7,"slippage_points":20,"max_positions":3,"max_spread_points":40}


@pytest.fixture
def live_engine(broker, risk_cfg, tmp_path):
    from core.execution import ExecutionEngine
    from core.risk_manager import RiskManager
    from database.repository import Repository
    from utils.types import TradingMode
    return ExecutionEngine(broker, TradingMode.LIVE, True, str(tmp_path / "kill.flag"), RiskManager(risk_cfg, broker),
                           Repository("sqlite:///" + str(tmp_path / "test.db")), risk_cfg, "test-account")
