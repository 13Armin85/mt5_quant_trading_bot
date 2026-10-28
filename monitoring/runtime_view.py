"""Read-only view of the shared SQLite journal; never construct a trading runtime."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from sqlalchemy.engine import make_url


def read_runtime(database_url):
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
        trades = [dict(r) for r in db.execute("SELECT symbol,direction,confidence,market_regime,lot,status,timestamp,reason FROM trades ORDER BY id DESC LIMIT 100")]
        positions = []
        scope = (states.get("heartbeat") or {}).get("scope")
        if scope:
            for row in db.execute("SELECT status,payload FROM execution_attempts WHERE key LIKE ? AND status IN ('OPEN','PARTIAL','PAPER_OPEN')", (scope + ":%",)):
                payload = json.loads(row["payload"])
                positions.append({"symbol":payload.get("symbol"),"profile":payload.get("profile","baseline"),
                    "direction":payload.get("direction"),"ticket":payload.get("ticket"),"status":row["status"]})
    return {"heartbeat":states.get("heartbeat"),"analyses":[v for k,v in states.items() if k.startswith("analysis:")],
        "trades":trades,"positions":positions}
