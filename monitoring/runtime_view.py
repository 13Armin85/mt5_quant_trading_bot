"""Read-only view of the shared SQLite journal; never construct a trading runtime."""
from contextlib import closing
import json
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
        trade_query = "SELECT symbol,direction,confidence,market_regime,lot,status,timestamp,reason FROM trades"
        trade_args = ()
        if scope:
            trade_query += " WHERE EXISTS (SELECT 1 FROM execution_attempts e WHERE e.key=? || ':' || trades.signal_id)"
            trade_args = (scope,)
        trades = [dict(r) for r in db.execute(trade_query + " ORDER BY id DESC LIMIT 100", trade_args)]
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
            for row in db.execute("SELECT status,payload FROM execution_attempts WHERE key LIKE ? AND status IN ('OPEN','PARTIAL','PAPER_OPEN')", (position_scope + ":%",)):
                payload = json.loads(row["payload"])
                positions.append({"symbol":payload.get("symbol"),"profile":payload.get("profile","baseline"),
                    "direction":payload.get("direction"),"ticket":payload.get("ticket"),"status":row["status"]})
    return {"heartbeat":states.get("heartbeat"),"analyses":[v for k,v in states.items() if k.startswith("analysis:")],
        "trades":trades,"positions":positions}
