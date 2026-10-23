from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from config import load_config
from database.repository import Repository
from docker_runtime.healthcheck import healthy
from scripts import docker_setup
from monitoring.runtime_view import read_runtime


def test_container_health_checks_only_recent_connected_heartbeat(tmp_path):
    path = tmp_path / "journal.db"
    repo = Repository("sqlite:///" + str(path))
    assert not healthy(path)
    repo.set_state("heartbeat", {"time": datetime.now(timezone.utc).isoformat(), "connected": True})
    assert healthy(path)
    repo.set_state("heartbeat", {"time": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), "connected": True})
    assert not healthy(path)
    repo.set_state("heartbeat", {"time": datetime.now(timezone.utc).isoformat(), "connected": False})
    assert not healthy(path)
    assert not healthy(tmp_path / "missing.db")


def test_setup_migrates_history_and_points_native_to_same_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_setup, "ROOT", tmp_path)
    cfg = load_config().model_copy(deep=True)
    cfg.env.enable_live_trading = True
    cfg.env.database_url = "sqlite:///original.db"
    cfg.env.runtime_lock_path = str(tmp_path / ".bot.lock")
    cfg.env.mt5_bridge_token_file = str(tmp_path / ".docker" / "bridge.token")
    (tmp_path / ".env").write_text("MT5_PASSWORD='keep-this-private'\nDATABASE_URL=sqlite:///original.db\n")
    with sqlite3.connect(tmp_path / "original.db") as db:
        db.execute("CREATE TABLE evidence (id INTEGER)")
        db.execute("INSERT INTO evidence VALUES (123)")
    docker_setup.prepare(cfg)
    with sqlite3.connect(tmp_path / ".docker" / "data" / "trading_bot.db") as db:
        assert db.execute("SELECT id FROM evidence").fetchone() == (123,)
    assert (tmp_path / "original.db").exists()
    assert "sqlite:///.docker/data/trading_bot.db" in (tmp_path / ".env").read_text()
    assert "keep-this-private" in (tmp_path / ".env").read_text()
    assert len((tmp_path / ".docker" / "bridge.token").read_text()) >= 32
    with pytest.raises(ValueError, match="Reconcile"):
        docker_setup.prepare(cfg)  # Must never overwrite an existing Docker journal.


def test_dashboard_reads_journal_without_creating_missing_database(tmp_path):
    missing = tmp_path / "not-created.db"
    with pytest.raises(sqlite3.OperationalError):
        read_runtime("sqlite:///" + str(missing))
    assert not missing.exists()
    target = tmp_path / "existing.db"
    repo = Repository("sqlite:///" + str(target))
    repo.set_state("heartbeat", {"mode":"LIVE","news":{"background_running":True}})
    repo.set_state("analysis:EURUSD", {"symbol":"EURUSD","direction":"HOLD"})
    view = read_runtime("sqlite:///" + str(target))
    assert view["heartbeat"]["mode"] == "LIVE" and len(view["analyses"]) == 1
    assert not view["trades"]


