from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event, get_ident
import time

from config import AppConfig, EnvSettings
from news.background import ConcurrentNewsFilter
from news.news_filter import NewsFilter
from news.news_provider import NewsEvent


def config(poll=60):
    return AppConfig(settings={"news":{"enabled":True,"fail_closed":True,"poll_seconds":poll,"max_snapshot_age_seconds":180}},
                     symbols={"symbols":{"XAUUSD":{"currencies":["XAU","USD"]}}},strategy={},
                     env=EnvSettings(_env_file=None, ECONOMIC_API_URL="https://calendar.invalid"))


def test_slow_http_does_not_block_chart_thread_and_entries_wait_for_snapshot():
    cfg = config()
    collector = NewsFilter(cfg)
    entered, release = Event(), Event()
    threads = []
    class SlowCalendar:
        def events(self, *args):
            threads.append(get_ident())
            entered.set()
            assert release.wait(3)
            return []
    collector.provider = SlowCalendar()
    news = ConcurrentNewsFilter(cfg, collector)
    news.start_background()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        news.refresh()
        allowed, reason, _ = news.evaluate("XAUUSD")
        assert time.monotonic() - started < .5
        assert not allowed and "pending" in reason
        assert threads[0] != get_ident()
        release.set()
        assert news.wait_ready(1)
        assert news.evaluate("XAUUSD")[0]
        assert news.status()["background_running"]
    finally:
        release.set(); news.stop_background()
    assert not news.status()["background_running"]
    assert not news.evaluate("XAUUSD")[0]


def test_published_news_snapshot_is_detached_from_provider_mutations():
    cfg = config()
    collector = NewsFilter(cfg)
    event = NewsEvent(datetime.now(timezone.utc) + timedelta(minutes=10), "USD", "HIGH", "CPI")
    class Calendar:
        def events(self, *args): return [event]
    collector.provider = Calendar()
    news = ConcurrentNewsFilter(cfg, collector)
    news.start_background()
    try:
        assert news.wait_ready(1)
        event.impact = "LOW"
        collector.cache.clear()
        allowed, reason, _ = news.evaluate("XAUUSD")
        assert not allowed and "CPI" in reason
        status = news.status()
        status["headlines"].append({"fake": True})
        assert not news.status()["headlines"]
    finally:
        news.stop_background()


def test_stale_background_snapshot_blocks_entries():
    cfg = config()
    collector = NewsFilter(cfg)
    class Calendar:
        def events(self, *args): return []
    collector.provider = Calendar()
    news = ConcurrentNewsFilter(cfg, collector)
    news.start_background()
    try:
        assert news.wait_ready(1)
        with news._lock:
            news._snapshot = replace(news._snapshot, published_at=datetime.now(timezone.utc) - timedelta(minutes=4))
        allowed, reason, _ = news.evaluate("XAUUSD")
        assert not allowed and "stale" in reason
    finally:
        news.stop_background()


def test_background_failure_does_not_allow_entries_or_expose_credentials():
    cfg = config()
    collector = NewsFilter(cfg)
    class FailedCalendar:
        def events(self, *args): raise ValueError("secret credential in provider error")
    collector.provider = FailedCalendar()
    news = ConcurrentNewsFilter(cfg, collector)
    news.start_background()
    try:
        assert news.wait_ready(1)
        allowed, reason, _ = news.evaluate("XAUUSD")
        assert not allowed and "secret" not in reason
        assert news.status()["last_error"] == "ValueError"
    finally:
        news.stop_background()
