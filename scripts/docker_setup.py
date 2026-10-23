"""Prepare local Docker data without starting the trader or sending any order."""
import argparse
import secrets
import sqlite3
from pathlib import Path

import httpx

from dotenv import set_key
from sqlalchemy.engine import make_url

from config import ROOT, load_config
from core.mt5_remote import RemoteMT5Client
from core.runtime_lock import RuntimeLock


def prepare(cfg):
    if not cfg.env.enable_live_trading:
        raise ValueError("Set ENABLE_LIVE_TRADING=true in .env to authorize LIVE startup")
    base = ROOT / ".docker"
    for path in (base, base / "data", base / "control", ROOT / "logs", ROOT / "reports"):
        path.mkdir(exist_ok=True)
    token_path = base / "bridge.token"
    if Path(cfg.env.mt5_bridge_token_file).resolve() != token_path.resolve():
        raise ValueError("Start-Live requires the default .docker/bridge.token location")
    if not token_path.exists():
        with token_path.open("x", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(48))
    if len(token_path.read_text().strip()) < 32:
        raise ValueError("Invalid bridge token file")
    url = make_url(cfg.env.database_url)
    if url.drivername != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("This Docker setup requires a persistent SQLite DATABASE_URL")
    source = Path(url.database)
    if not source.is_absolute():
        source = ROOT / source
    target = base / "data" / "trading_bot.db"
    if source.resolve() != target.resolve():
        with RuntimeLock(cfg.env.runtime_lock_path):
            if target.exists():
                raise ValueError("Docker journal already exists but DATABASE_URL differs. Reconcile databases before switching; no history was overwritten")
            if source.exists():
                with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
                    src.backup(dst)
                print("Existing SQLite journal copied consistently; original retained.")
            # Native runs now use the SAME persistent journal as the Docker engine.
            set_key(str(ROOT / ".env"), "DATABASE_URL", "sqlite:///.docker/data/trading_bot.db")
    if (ROOT / cfg.get("trading.kill_switch_path", "kill_switch.flag")).exists():
        (base / "control" / "kill_switch.flag").touch()
    print("Docker data/secret ready. Account password remains on Windows.")


def probe(cfg):
    env = cfg.env.model_copy(update={"mt5_bridge_url": "http://127.0.0.1:18765"})
    client = RemoteMT5Client(env, cfg.get("trading.mt5_time_zone", "UTC"))
    client.http.timeout = httpx.Timeout(3)
    try:
        client.initialize()
        return client.health_check() and client.bridge_live
    except (OSError, RuntimeError):
        return False
    finally:
        client.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--stop-bridge", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    if args.probe:
        raise SystemExit(0 if probe(cfg) else 1)
    if args.stop_bridge:
        env = cfg.env.model_copy(update={"mt5_bridge_url": "http://127.0.0.1:18765"})
        client = RemoteMT5Client(env, cfg.get("trading.mt5_time_zone", "UTC"))
        try:
            client._rpc("stop_bridge")
        finally:
            client.shutdown()
        return
    prepare(cfg)


if __name__ == "__main__":
    main()
