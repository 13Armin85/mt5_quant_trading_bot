"""Public publisher feeds with persistent, rate-limited HTTP caching."""
from __future__ import annotations

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from core.runtime_lock import RuntimeLock
from news.news_provider import NewsEvent, deduplicate, parse_time


UTC = timezone.utc
FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
MAJOR_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY"}


class FeedUnavailable(ValueError):
    pass


class CachedPublicFeed:
    """Share successful downloads and failure backoff between CLI/runtime processes."""
    def __init__(self, url, cache_dir, interval=300, timeout=10):
        self.url, self.interval, self.timeout = url, max(60, int(interval)), timeout
        self.path = Path(cache_dir) / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        self.metadata = {}

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, value):
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        with RuntimeLock(self.path.with_suffix(".lock")):
            state = self._read()
            self.metadata = state
            retry = parse_time(state["retry_at"]) if state.get("retry_at") else None
            if retry and now < retry:
                if state.get("error"):
                    raise FeedUnavailable("Public feed is unavailable; waiting for retry")
                return state["body"]
            headers = {"User-Agent": "MT5QuantBot/1.0 (economic-calendar and RSS reader)"}
            if state.get("etag"):
                headers["If-None-Match"] = state["etag"]
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(self.url, headers=headers)
                if response.status_code == 304 and state.get("body"):
                    body = state["body"]
                else:
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise FeedUnavailable("Public feed exceeds size limit")
                    # XML parsers receive the original bytes (including its encoding).
                    body = response.content.hex()
                age = max(0, int(response.headers.get("Age", 0)))
                checked = now - timedelta(seconds=age)
                state = {"body": body, "checked_at": checked.isoformat(),
                         "retry_at": (now + timedelta(seconds=self.interval)).isoformat(),
                         "etag": response.headers.get("etag") or (state.get("etag") if response.status_code == 304 else None), "error": None,
                         "http_status": response.status_code,
                         "last_modified": response.headers.get("last-modified")}
            except Exception as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                delay = max(300, self.interval)
                if isinstance(exc, httpx.HTTPStatusError) and status == 429:
                    retry_after = exc.response.headers.get("retry-after", "")
                    try:
                        seconds = int(retry_after)
                    except ValueError:
                        try:
                            seconds = int((parsedate_to_datetime(retry_after) - now).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            seconds = 0
                    delay = max(delay, seconds)
                state.update(error=type(exc).__name__, http_status=status,
                             retry_at=(now + timedelta(seconds=delay)).isoformat())
                self._write(state); self.metadata = state
                raise FeedUnavailable("Public feed request failed; retry scheduled") from None
            self._write(state); self.metadata = state
            return state["body"]

    def content(self):
        return bytes.fromhex(self.get())

    @property
    def checked_at(self):
        return parse_time(self.metadata["checked_at"]) if self.metadata.get("checked_at") else None


class ForexFactoryProvider:
    """Official weekly export: schedule/impact/forecast, NOT a live actuals API."""
    def __init__(self, cache_dir, timeout=10, refresh_seconds=3600):
        self.feed = CachedPublicFeed(FOREX_FACTORY_URL, cache_dir, max(3600, refresh_seconds), timeout)
        self.source_updated_at = None
        self.coverage_start = self.coverage_end = None
        self.all_events = []
        self.raw_count = 0

    def events(self, start, end):
        rows = json.loads(self.feed.content())
        if not isinstance(rows, list) or not rows:
            raise FeedUnavailable("Weekly calendar is empty or invalid")
        self.raw_count = len(rows)
        events, weeks = [], set()
        eastern = ZoneInfo("America/New_York")
        for row in rows:
            ts = parse_time(row["date"])
            local = ts.astimezone(eastern)
            sunday = local.date() - timedelta(days=(local.weekday() + 1) % 7)
            weeks.add(sunday)
            impact, currency = str(row["impact"]).upper(), str(row["country"]).upper()
            if impact == "HOLIDAY":
                continue
            if impact not in {"LOW", "MEDIUM", "HIGH"}:
                raise FeedUnavailable("Unknown calendar impact")
            if currency == "ALL":
                currencies = MAJOR_CURRENCIES
            elif currency in MAJOR_CURRENCIES:
                currencies = {currency}
            else:
                raise FeedUnavailable("Unknown calendar currency")
            title = str(row["title"]).strip()
            if not title:
                raise FeedUnavailable("Calendar title missing")
            for mapped in sorted(currencies):
                key = hashlib.sha256(f"{mapped}:{ts.isoformat()}:{title}".encode()).hexdigest()[:24]
                events.append(NewsEvent(ts, mapped, impact, title, actual=row.get("actual") or None,
                    forecast=row.get("forecast") or None, previous=row.get("previous") or None,
                    source="Forex Factory", event_id=key))
        if len(weeks) != 1:
            raise FeedUnavailable("Weekly calendar contains inconsistent dates")
        sunday = next(iter(weeks))
        self.coverage_start = datetime.combine(sunday, datetime.min.time(), eastern).astimezone(UTC)
        self.coverage_end = datetime.combine(sunday + timedelta(days=7), datetime.min.time(), eastern).astimezone(UTC)
        if not self.covers(datetime.now(UTC), datetime.now(UTC)):
            raise FeedUnavailable("Weekly calendar does not cover the current date")
        self.all_events = deduplicate(events)
        self.source_updated_at = self.feed.checked_at
        return [e for e in self.all_events if start <= e.timestamp <= end]

    def covers(self, start, end):
        return self.coverage_start is not None and self.coverage_start <= start <= end < self.coverage_end


def _text(element, name):
    return next((child.text.strip() for child in element if child.tag.split("}")[-1] == name and child.text), "")


class OfficialRSSProvider:
    """Publisher/currency mappings and risk flags; headline text cannot place orders."""
    def __init__(self, sources, cache_dir, timeout=10, refresh_seconds=300):
        self.sources = [dict(source, feed=CachedPublicFeed(source["url"], cache_dir, refresh_seconds, timeout))
                        for source in sources if source.get("enabled", True)]
        self.source_updated_at = None
        self.statuses = []
        self.failed_currencies = set()
        self.all_events = []

    @staticmethod
    def parse(content, source):
        declarations = content.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
            raise FeedUnavailable("DTD/entity declarations are not accepted in feeds")
        root = ET.fromstring(content)
        if root.tag.split("}")[-1] not in {"rss", "RDF", "feed"}:
            raise FeedUnavailable("Expected RSS/Atom feed")
        rows, events = [e for e in root.iter() if e.tag.split("}")[-1] in {"item", "entry"}], []
        if not rows:
            raise FeedUnavailable("RSS feed contains no entries")
        for row in rows:
            title = _text(row, "title")
            value = _text(row, "pubDate") or _text(row, "published") or _text(row, "date") or _text(row, "updated")
            if not title or not value:
                raise FeedUnavailable("RSS item requires title and publication time")
            try:
                ts = parse_time(value)
            except ValueError:
                ts = parsedate_to_datetime(value)
                if ts.tzinfo is None:
                    raise FeedUnavailable("RSS publication time needs a timezone")
                ts = ts.astimezone(UTC)
            if ts > datetime.now(UTC) + timedelta(minutes=5):
                raise FeedUnavailable("RSS publication time is unexpectedly in the future")
            impact = source.get("default_impact", "LOW")
            if any(word.lower() in title.lower() for word in source.get("high_impact_keywords", [])):
                impact = "HIGH"
            identifier = _text(row, "guid") or _text(row, "id") or _text(row, "link") or title
            link = _text(row, "link") or next((e.get("href", "") for e in row if e.tag.split("}")[-1] == "link"), "")
            for currency in source["currencies"]:
                events.append(NewsEvent(ts, currency, impact, title, source=source["name"],
                    event_id=f"{identifier}:{currency}", published_at=ts, kind="headline", url=link))
        return deduplicate(events)

    def events(self, start, end):
        self.statuses, self.failed_currencies, events, checked = [], set(), [], []
        # Each source caches independently; one failed publisher doesn't erase others.
        for source in self.sources:
            status = {"name": source["name"], "currencies": source["currencies"],
                      "required": source.get("required", True), "ok": False}
            try:
                rows = self.parse(source["feed"].content(), source)
                stamp = source["feed"].checked_at
                if stamp is None or (datetime.now(UTC) - stamp).total_seconds() > 7200:
                    raise FeedUnavailable("RSS HTTP snapshot is stale")
                events.extend(rows); checked.append(stamp)
                status.update(ok=True, item_count=len(rows), checked_at=stamp.isoformat(),
                              latest_published_at=max(e.timestamp for e in rows).isoformat())
            except Exception as exc:
                status.update(error=type(exc).__name__, http_status=source["feed"].metadata.get("http_status"))
                if status["required"]:
                    self.failed_currencies.update(source["currencies"])
            self.statuses.append(status)
        self.source_updated_at = min(checked) if checked else None
        self.all_events = deduplicate(events)
        return [e for e in self.all_events if start <= e.timestamp <= end]
