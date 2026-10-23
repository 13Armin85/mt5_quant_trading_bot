from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


@dataclass(slots=True)
class NewsEvent:
    timestamp: datetime
    currency: str
    impact: str
    title: str
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    source: str = "calendar"
    event_id: str = ""
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    published_at: datetime | None = None
    unit: str = ""
    revision: str | None = None


def parse_time(value, assume_utc=False):
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        if not assume_utc:
            raise ValueError("News timestamps must include timezone")
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def deduplicate(events):
    latest = {}
    for event in events:
        if not event.currency or not event.title or event.impact not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("Invalid news event")
        key = (event.source, event.event_id or (event.timestamp, event.currency, event.title))
        previous = latest.get(key)
        if previous is None or (event.updated_at or event.received_at) >= (previous.updated_at or previous.received_at):
            latest[key] = event
    return sorted(latest.values(), key=lambda e: e.timestamp)


class EconomicCalendarProvider(ABC):
    @abstractmethod
    def events(self, start: datetime, end: datetime) -> list[NewsEvent]: ...


class DisabledCalendarProvider(EconomicCalendarProvider):
    def events(self, start: datetime, end: datetime) -> list[NewsEvent]:
        return []


class GenericJsonCalendarProvider(EconomicCalendarProvider):
    """Provider for a user-configured real HTTP JSON calendar endpoint.

    Expected response: list or {'events': [...]} with timestamp/currency/impact/title fields.
    No vendor endpoint is invented; URL and key must be supplied by the user.
    """
    def __init__(self, url: str, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.url, self.api_key, self.timeout = url, api_key, timeout

    def events(self, start: datetime, end: datetime) -> list[NewsEvent]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        params = {"start": start.isoformat(), "end": end.isoformat()}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(self.url, params=params, headers=headers); r.raise_for_status(); payload = r.json()
        rows = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Calendar response must be a list or contain events")
        self.source_updated_at = parse_time(payload["generated_at"]) if isinstance(payload, dict) and payload.get("generated_at") else datetime.now(timezone.utc)
        result = []
        for row in rows:
            result.append(NewsEvent(parse_time(row["timestamp"]), str(row["currency"]).upper(), str(row["impact"]).upper(), str(row["title"]), row.get("actual"), row.get("forecast"), row.get("previous"), source=str(row.get("source", "generic-calendar")), event_id=str(row.get("id", "")), updated_at=parse_time(row["updated_at"]) if row.get("updated_at") else None, published_at=parse_time(row["published_at"]) if row.get("published_at") else None, unit=str(row.get("unit", "")), revision=row.get("revision")))
        return deduplicate(result)


class TradingEconomicsProvider(EconomicCalendarProvider):
    """Official calendar API. Subscription/coverage must be verified by the operator."""
    def __init__(self, api_key, timeout=10):
        self.api_key, self.timeout = api_key, timeout

    def events(self, start, end):
        if not self.api_key:
            raise ValueError("ECONOMIC_API_KEY required")
        url = f"https://api.tradingeconomics.com/calendar/country/All/{start:%Y-%m-%d}/{end:%Y-%m-%d}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(url, params={"c": self.api_key, "f": "json"})
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("Invalid Trading Economics response")
        countries = {"United States": "USD", "United Kingdom": "GBP", "Japan": "JPY", "Euro Area": "EUR", "Germany": "EUR", "France": "EUR", "Italy": "EUR", "Spain": "EUR", "Australia": "AUD", "New Zealand": "NZD", "Canada": "CAD", "Switzerland": "CHF", "China": "CNY"}
        result = []
        for row in rows:
            currency = countries.get(row.get("Country")) or row.get("Currency")
            if not currency:
                continue
            impact = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}.get(int(row["Importance"]))
            if impact is None or int(row.get("DateSpan", 0)) != 0:
                raise ValueError("Calendar contains uncertain event time/importance")
            result.append(NewsEvent(parse_time(row["Date"], True), currency, impact, row["Event"], row.get("Actual"), row.get("Forecast"), row.get("Previous"), source="Trading Economics", event_id=str(row["CalendarId"]), updated_at=parse_time(row["LastUpdate"], True) if row.get("LastUpdate") else None, unit=row.get("Unit", ""), revision=row.get("Revised")))
        self.source_updated_at = datetime.now(timezone.utc)
        return deduplicate(result)


class JsonHeadlineProvider:
    """User-authorized JSON endpoint: articles with timestamp/currencies/title/impact.

    Headlines only suspend entries; prose sentiment never determines order direction.
    """
    def __init__(self, url, key=None, timeout=10):
        self.url, self.key, self.timeout = url, key, timeout

    def events(self, start, end):
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(self.url, params={"start": start.isoformat(), "end": end.isoformat()}, headers=headers)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("articles") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Invalid headline response")
        self.source_updated_at = parse_time(payload["generated_at"]) if isinstance(payload, dict) and payload.get("generated_at") else datetime.now(timezone.utc)
        events = []
        for row in rows:
            ts = parse_time(row["timestamp"])
            currencies = row["currencies"]
            if not isinstance(currencies, list) or not currencies:
                raise ValueError("Headline needs explicit currency mapping")
            for currency in currencies:
                events.append(NewsEvent(ts, str(currency).upper(), str(row["impact"]).upper(), row["title"], source=row.get("source", "headlines"), event_id=f"{row.get('id', row['title'])}:{currency}", published_at=ts, updated_at=parse_time(row["updated_at"]) if row.get("updated_at") else None))
        return deduplicate(events)
