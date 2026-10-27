"""Read the engine heartbeat; do not connect to MT5 or construct an execution engine."""
import json
import sqlite3
import sys
from datetime import datetime, timezone


def healthy(path="/app/data/trading_bot.db", max_age=180):
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as db:
            row = db.execute("SELECT payload FROM runtime_state WHERE key='heartbeat'").fetchone()
        state = json.loads(row[0])
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(state["time"])).total_seconds()
        return state.get("connected") is True and 0 <= age <= max_age
    except (OSError, sqlite3.Error, TypeError, KeyError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(0 if healthy() else 1)
