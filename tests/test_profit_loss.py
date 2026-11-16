from datetime import datetime, timezone
from types import SimpleNamespace as Obj

import pytest
from sqlalchemy.orm import Session

from database.models import ExecutionAttempt, Trade
from database.repository import Repository
from monitoring.runtime_view import read_runtime
from monitoring.profit_loss import mark_open_positions
from dashboard.workspace import trade_profit


def test_lifetime_net_is_scoped_and_not_limited_to_last_100_or_login(tmp_path):
    url = "sqlite:///" + str(tmp_path / "journal.db")
    repo = Repository(url)
    with Session(repo.engine) as db, db.begin():
        for index in range(105):
            scope = "LIVE:owner:123" if index < 104 else "LIVE:other:123"
            db.add(ExecutionAttempt(key=f"{scope}:{index}", status="CLOSED", payload="{}"))
            db.add(Trade(signal_id=str(index), symbol="EURUSD", direction="BUY", confidence=.8,
                market_regime="TRENDING", reason="test", status="CLOSED", ticket=index + 1,
                profit=(-.04 if index == 0 else 1 if index < 104 else 99999)))
    view = read_runtime(url, scope="LIVE:owner:123", since=datetime.now(timezone.utc).timestamp() + 100)
    assert len(view["trades"]) == 100
    assert view["pnl"]["closed_count"] == 104
    assert view["pnl"]["closed_net"] == pytest.approx(102.96)
    assert view["pnl"]["total_net"] == pytest.approx(102.96)
    assert view["pnl"]["gross_losses"] == -.04
    assert all(t["ticket"] != 105 for t in view["trades"])


def test_missing_settlement_is_unknown_but_zero_is_a_result(tmp_path):
    url = "sqlite:///" + str(tmp_path / "journal.db")
    repo = Repository(url)
    with Session(repo.engine) as db, db.begin():
        for index, profit in enumerate((0, None, float("inf"))):
            db.add(Trade(signal_id=str(index), symbol="A", direction="BUY", confidence=.5,
                market_regime="RANGING", reason="test", status="PAPER_CLOSED", profit=profit))
    pnl = read_runtime(url)["pnl"]
    assert pnl["closed_count"] == 3 and pnl["unsettled_count"] == 2
    assert pnl["closed_net"] is None and pnl["total_net"] is None
    assert trade_profit({"status":"CLOSED", "profit":0}, []) == 0
    assert trade_profit({"status":"OPEN", "ticket":None}, [{"ticket":None, "profit":9}]) is None


def snapshot(status="OPEN"):
    return {"positions":[{"ticket":11, "position_ticket":21, "magic":123, "symbol":"A",
        "status":status, "direction":"BUY", "volume":.1, "price":100, "partial_realized":2}],
        "pnl":{"closed_net":-1, "open_net":None, "total_net":None}}


class Client:
    def __init__(self, login=1):
        self.login = login
        self.position = Obj(ticket=21, identifier=11, magic=123, symbol="A", profit=10,
            swap=-.5, volume=.1, type=0)
        self.deals = [Obj(position_id=11, profit=0, commission=-.04, swap=0, fee=-.01),
            Obj(position_id=11, profit=2, commission=-.02, swap=-.1, fee=0)]
        self.closed = False
    def initialize(self): pass
    def shutdown(self): self.closed = True
    def account_info(self): return Obj(login=self.login, server="LiteFinance-MT5-Demo")
    def positions_get(self): return [self.position]
    def history_deals_get(self, *, position):
        assert position == 11
        return self.deals
    def symbol_info_tick(self, symbol): return Obj(bid=101, ask=102)
    def order_calc_profit(self, *args): return 10


ACCOUNT = {"login":1, "server":"LiteFinance-MT5-Demo"}


def test_open_net_includes_fees_swap_partial_exits_and_only_journal_positions():
    client, view = Client(), snapshot()
    mark_open_positions(view, client, ACCOUNT)
    assert client.closed
    assert view["positions"][0]["profit"] == pytest.approx(11.33)
    assert view["pnl"]["total_net"] == pytest.approx(10.33)
    assert trade_profit({"status":"OPEN", "ticket":11}, view["positions"]) == pytest.approx(11.33)


@pytest.mark.parametrize("failure", ["wrong_account", "manual_position", "history_error", "partial_exit", "unknown"])
def test_unavailable_or_changing_positions_never_show_false_zero(failure):
    client, view = Client(), snapshot()
    if failure == "wrong_account": client.login = 2
    if failure == "manual_position": client.position.magic = 999
    if failure == "history_error": client.deals = None
    if failure == "unknown": view["positions"][0]["status"] = "UNKNOWN"
    if failure == "partial_exit":
        def changing(*, position):
            client.position = Obj(**{**vars(client.position), "volume":.05})
            return client.deals
        client.history_deals_get = changing
    mark_open_positions(view, client, ACCOUNT)
    assert client.closed and view["pnl"]["open_net"] is None
    assert view["pnl"]["total_net"] is None
    assert view["positions"][0].get("profit") is None


def test_paper_net_uses_simulated_costs_and_partial_realized():
    view, client = snapshot("PAPER_OPEN"), Client()
    mark_open_positions(view, client, ACCOUNT,
        paper_commission=lambda symbol, price:4)
    assert view["pnl"]["open_net"] == pytest.approx(11.6)
    assert view["pnl"]["total_net"] == pytest.approx(10.6)
