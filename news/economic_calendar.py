from __future__ import annotations

from datetime import datetime, timedelta, timezone


class EconomicCalendar:
    def __init__(self, provider) -> None:
        self.provider = provider
        self.last_success = None

    def around_now(self, minutes_before: int, minutes_after: int):
        now = datetime.now(timezone.utc)
        events = self.provider.events(now - timedelta(minutes=minutes_after), now + timedelta(minutes=minutes_before))
        self.last_success = now
        return events
