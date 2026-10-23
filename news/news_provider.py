from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
        rows = payload.get("events", []) if isinstance(payload, dict) else payload
        result = []
        for row in rows:
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
            result.append(NewsEvent(ts, str(row.get("currency", "")).upper(), str(row.get("impact", "LOW")).upper(), str(row.get("title", "")), row.get("actual"), row.get("forecast"), row.get("previous")))
        return result
