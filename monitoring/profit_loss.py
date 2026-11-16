"""Read-only mark-to-market for journal-owned positions. Never submit orders."""
from datetime import datetime, timezone
import math

from core.mt5_client import MT5Error


def finite(value):
    if value is None:
        raise ValueError("P&L unavailable")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("P&L unavailable")
    return result


def mark_open_positions(view, client, account, *, paper_commission=None):
    """Add current net results; an unavailable or changing position stays unknown.

    Closed results already include all deal costs in the journal. For an open
    live position, add its floating profit/swap to booked deals (entry fees and
    partial exits). This does not estimate future closing commissions.
    """
    rows = view.get("positions") or []
    if not rows:
        return view
    try:
        client.initialize()
        actual = client.account_info()
        if (int(actual.login), str(actual.server)) != (int(account["login"]), account["server"]):
            return view
        live_rows = [r for r in rows if r["status"] != "PAPER_OPEN"]
        positions = client.positions_get() if live_rows else ()
        if positions is None:
            return view
        marks = {}
        for index, row in enumerate(rows):
            try:
                if row["status"] == "PAPER_OPEN":
                    if paper_commission is None:
                        continue
                    tick = client.symbol_info_tick(row["symbol"])
                    if tick is None:
                        continue
                    direction = 0 if row["direction"] == "BUY" else 1
                    mark = tick.bid if direction == 0 else tick.ask
                    floating = finite(client.order_calc_profit(direction, row["symbol"],
                        row["volume"], row["price"], mark))
                    cost = finite(paper_commission(row["symbol"], row["price"])) * finite(row["volume"])
                    marks[index] = floating - cost + finite(row.get("partial_realized") or 0)
                    continue
                if row["status"] in {"UNKNOWN", "SUBMITTING"}:
                    continue
                position = next((p for p in positions
                    if p.ticket == (row.get("position_ticket") or row.get("ticket"))
                    and p.magic == row.get("magic") and p.symbol == row["symbol"]), None)
                if position is None:
                    continue
                identifier = int(position.identifier)
                deals = client.history_deals_get(position=identifier)
                if not deals or any(int(d.position_id) != identifier for d in deals):
                    continue
                booked = math.fsum(finite(d.profit) + finite(d.commission) + finite(d.swap)
                    + finite(getattr(d, "fee", 0)) for d in deals)
                marks[index] = finite(position.profit) + finite(position.swap) + booked
            except (MT5Error, ValueError, TypeError, AttributeError, OverflowError):
                continue
        # A partial exit during the history request must not be counted twice.
        after = client.positions_get() if live_rows else ()
        if after is None:
            return view
        signatures = lambda items: {(p.ticket, p.identifier, p.volume, p.type) for p in items}
        if signatures(positions) != signatures(after):
            marks = {i: value for i, value in marks.items() if rows[i]["status"] == "PAPER_OPEN"}
        for index, value in marks.items():
            rows[index]["profit"] = finite(value)
        pnl = view["pnl"]
        pnl["open_net"] = math.fsum(marks.values()) if len(marks) == len(rows) else None
        pnl["total_net"] = (pnl["closed_net"] + pnl["open_net"]
            if pnl["closed_net"] is not None and pnl["open_net"] is not None else None)
        pnl["marked_at"] = datetime.now(timezone.utc).isoformat()
    except (MT5Error, ValueError, TypeError, AttributeError, OverflowError):
        pass
    finally:
        client.shutdown()
    return view
