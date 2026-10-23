"""Keep HTTP work off the MT5 thread and publish complete news snapshots."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from types import SimpleNamespace

from loguru import logger

from news.news_filter import NewsFilter


@dataclass(frozen=True)
class Coverage:
    start: datetime
    end: datetime

    def covers(self, start, end):
        return self.start is not None and self.start <= start <= end < self.end


@dataclass(frozen=True)
class Snapshot:
    reader: NewsFilter
    status: dict
    published_at: datetime
    generation: int


class ConcurrentNewsFilter:
    def __init__(self, cfg, collector=None):
        self.cfg = cfg
        self.collector = collector if collector is not None else NewsFilter(cfg)
        self._initial_status = self.collector.status()
        self._snapshot = None
        self._decision_reader = None
        self._lock = Lock()
        self._stop = Event()
        self._refreshing = Event()
        self._ready = Event()
        self._thread = None
        self._started = False
        self._worker_error = None

    def start_background(self):
        if self._started:
            return
        self._started = True
        if not self.cfg.get("news.enabled", True):
            return
        self._thread = Thread(target=self._run, name="news-feeds", daemon=True)
        self._thread.start()

    def stop_background(self, timeout=2):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def wait_ready(self, timeout):
        return self._ready.wait(timeout)

    def _capture(self, generation):
        # Only the worker touches HTTP providers. The chart thread gets copies of
        # events, source health and coverage from the SAME completed refresh.
        source = self.collector
        reader = object.__new__(NewsFilter)
        reader.cfg, reader.available = self.cfg, source.available
        reader.cache = deepcopy(source.cache)
        reader.last_poll, reader.last_success, reader.last_error = source.last_poll, source.last_success, source.last_error
        reader.last_analysis = []
        reader.provider = (Coverage(getattr(source.provider, "coverage_start", None),
                                    getattr(source.provider, "coverage_end", None))
                           if hasattr(source.provider, "covers") else SimpleNamespace())
        reader.headlines = SimpleNamespace(failed_currencies=frozenset(getattr(source.headlines, "failed_currencies", set())))
        reader.refresh = lambda now=None: None
        status = deepcopy(source.status())
        def summary(event):
            return {"time":event.timestamp.isoformat(),"currency":event.currency,"impact":event.impact,
                    "title":event.title,"source":event.source,"url":event.url}
        headlines = getattr(source.headlines, "all_events", [e for e in source.cache if e.kind == "headline"])
        status["latest_headlines"] = [summary(e) for e in sorted(headlines, key=lambda e:e.timestamp, reverse=True)[:20]]
        status["upcoming_events"] = [summary(e) for e in source.cache if e.kind == "release" and e.timestamp >= datetime.now(timezone.utc)][:20]
        return Snapshot(reader, status, datetime.now(timezone.utc), generation)

    def _run(self):
        interval = float(self.cfg.get("news.poll_seconds", 60))
        generation = 0
        while not self._stop.is_set():
            published = False
            self._refreshing.set()
            try:
                self.collector.refresh()
                generation += 1
                snapshot = self._capture(generation)
                with self._lock:
                    self._snapshot = snapshot
                    self._worker_error = None
                published = True
            except Exception as exc:
                with self._lock:
                    self._worker_error = type(exc).__name__
                logger.warning("Background news update failed type={}", type(exc).__name__)
            finally:
                self._refreshing.clear()
            if published:
                self._ready.set()
            self._stop.wait(interval)

    def _current(self):
        with self._lock:
            return self._snapshot, self._worker_error

    def refresh(self, now=None):
        if not self._started:
            self.collector.refresh(now)
        # During run, refresh() deliberately does no HTTP and takes no long lock.

    def currencies_for(self, symbol):
        return self.collector.currencies_for(symbol)

    def evaluate(self, symbol, now=None):
        if not self._started or not self.cfg.get("news.enabled", True):
            return self.collector.evaluate(symbol, now)
        self._decision_reader = None
        snapshot, error = self._current()
        fail_closed = bool(self.cfg.get("news.fail_closed", True))
        if self._stop.is_set() or self._thread is None or not self._thread.is_alive() or error:
            return not fail_closed, "Background news unavailable; new entries suspended", 0.0
        if snapshot is None or self._refreshing.is_set():
            return not fail_closed, "News update pending; chart analysis continues", 0.0
        now = now or datetime.now(timezone.utc)
        age = (now - snapshot.published_at).total_seconds()
        if not 0 <= age <= float(self.cfg.get("news.max_snapshot_age_seconds", 180)):
            return not fail_closed, "Background news snapshot is stale; new entries suspended", 0.0
        self._decision_reader = snapshot.reader
        return snapshot.reader.evaluate(symbol, now)

    def direction_allowed(self, symbol, direction):
        if not self._started:
            return self.collector.direction_allowed(symbol, direction)
        return self._decision_reader.direction_allowed(symbol, direction) if self._decision_reader else True

    def status(self):
        if not self._started:
            result = self.collector.status()
        else:
            snapshot, error = self._current()
            result = deepcopy(snapshot.status if snapshot else self._initial_status)
            result.update(snapshot_generation=snapshot.generation if snapshot else 0,
                          snapshot_published_at=snapshot.published_at.isoformat() if snapshot else None,
                          snapshot_age_seconds=round((datetime.now(timezone.utc) - snapshot.published_at).total_seconds(), 1) if snapshot else None,
                          worker_error=error)
        result.update(background_running=bool(self._thread and self._thread.is_alive()),
                      refresh_in_progress=self._refreshing.is_set())
        return result
