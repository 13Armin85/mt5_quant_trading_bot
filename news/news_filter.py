from __future__ import annotations

from datetime import datetime, timedelta, timezone
from loguru import logger

from news.news_provider import DisabledCalendarProvider, GenericJsonCalendarProvider


class NewsFilter:
    def __init__(self, app_cfg) -> None:
        self.cfg = app_cfg
        ncfg = app_cfg.get("news", {})
        env = app_cfg.env
        if ncfg.get("enabled", True) and env.economic_api_url:
            self.provider = GenericJsonCalendarProvider(env.economic_api_url, env.economic_api_key)
            self.available = True
        else:
            self.provider = DisabledCalendarProvider(); self.available = False

    def currencies_for(self, symbol: str) -> set[str]:
        meta = self.cfg.symbols.get("symbols", {}).get(symbol, {})
        return set(meta.get("currencies", [])) | set(meta.get("extra_news_currencies", []))

    def evaluate(self, symbol: str) -> tuple[bool, str, float]:
        ncfg = self.cfg.get("news", {})
        if not ncfg.get("enabled", True):
            return True, "News filtering disabled by config", 0.5
        if not self.available:
            if ncfg.get("fail_closed", True):
                return False, "Economic calendar unavailable (fail-closed)", 0.0
            return True, "Economic calendar unavailable (fail-open configured)", 0.0
        now = datetime.now(timezone.utc)
        before = int(ncfg.get("block_before_minutes", 30)); after = int(ncfg.get("block_after_minutes", 30))
        try:
            events = self.provider.events(now - timedelta(minutes=after), now + timedelta(minutes=before))
        except Exception as exc:
            logger.error("News provider failure: {}", exc)
            return (False, "Economic calendar provider failure", 0.0) if ncfg.get("fail_closed", True) else (True, "News provider failure", 0.0)
        currencies = self.currencies_for(symbol)
        blocked = [e for e in events if e.currency in currencies and e.impact == "HIGH"]
        if blocked:
            nearest = min(blocked, key=lambda e: abs((e.timestamp-now).total_seconds()))
            mins = abs((nearest.timestamp-now).total_seconds()) / 60
            return False, f"High-impact {nearest.currency} event '{nearest.title}' within {mins:.0f}m", 0.0
        return True, "News risk low", 0.85
