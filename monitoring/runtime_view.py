"""Read-only view of the shared SQLite journal; never construct a trading runtime."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
from datetime import datetime

from sqlalchemy.engine import make_url


def read_runtime(database_url, *, scope=None, since=None):
    url = make_url(database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Docker dashboard requires the persistent SQLite journal")
    address = Path(url.database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(address, uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        states = {r["key"]:json.loads(r["payload"]) for r in db.execute(
            "SELECT key,payload FROM runtime_state WHERE key='heartbeat' OR key LIKE 'analysis:%'")}
        from core.trading_preferences import DEFAULTS
        saved = db.execute("SELECT payload FROM runtime_state WHERE key=?", ((scope or "") + ":preferences",)).fetchone()
        preferences = json.loads(saved["payload"]) if saved else dict(DEFAULTS)
        trade_filter = ""
        trade_args = ()
        if scope:
            trade_filter = " WHERE EXISTS (SELECT 1 FROM execution_attempts e WHERE e.key=? || ':' || trades.signal_id)"
            trade_args = (scope,)
        trade_query = "SELECT symbol,direction,confidence,market_regime,lot,status,timestamp,reason,ticket,entry,exit_price,profit FROM trades" + trade_filter
        trades = [dict(r) for r in db.execute(trade_query + " ORDER BY id DESC LIMIT 100", trade_args)]
        # Aggregate the entire scoped history, independently of the table limit
        # and browser session start. Missing settlements are never treated as zero.
        totals_query = "SELECT profit,status FROM trades" + trade_filter
        settled = [r["profit"] for r in db.execute(totals_query, trade_args)
            if r["status"] in {"CLOSED", "PAPER_CLOSED"}]
        known = [p for p in settled if isinstance(p, (int, float)) and math.isfinite(p)]
        pnl = {"closed_count": len(settled), "unsettled_count": len(settled) - len(known),
            "closed_net": math.fsum(known) if len(known) == len(settled) else None,
            "gross_gains": math.fsum(p for p in known if p > 0),
            "gross_losses": math.fsum(p for p in known if p < 0)}
        positions = []
        heartbeat_scope = (states.get("heartbeat") or {}).get("scope")
        heartbeat_time = (states.get("heartbeat") or {}).get("time")
        if scope and (heartbeat_scope != scope or (since and heartbeat_time and datetime.fromisoformat(heartbeat_time).timestamp() < since)):
            states = {}
        if since:
            states = {key: value for key, value in states.items() if key == "heartbeat" or (
                isinstance(value, dict) and value.get("updated_at") and datetime.fromisoformat(value["updated_at"]).timestamp() >= since)}
        position_scope = scope or heartbeat_scope
        if position_scope:
            for row in db.execute("SELECT status,payload FROM execution_attempts WHERE substr(key,1,length(?)+1)=? || ':' AND status IN ('OPEN','PARTIAL','PAPER_OPEN','PROTECTION_FAILURE','UNKNOWN','SUBMITTING')", (position_scope, position_scope)):
                payload = json.loads(row["payload"])
                positions.append({"symbol":payload.get("symbol"),"profile":payload.get("profile","baseline"),
                    "direction":payload.get("direction"),"ticket":payload.get("ticket"),"status":row["status"],
                    **{key: payload.get(key) for key in ("position_ticket", "magic", "price", "volume", "partial_realized")}})
        pnl.update(open_net=0.0 if not positions else None)
        pnl["total_net"] = pnl["closed_net"] if not positions else None
    return {"heartbeat":states.get("heartbeat"),"analyses":[v for k,v in states.items() if k.startswith("analysis:")],
        "trades":trades,"positions":positions,"pnl":pnl,"trading_preferences":preferences}
