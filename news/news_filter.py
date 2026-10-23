from __future__ import annotations

from datetime import datetime, timedelta, timezone
from loguru import logger

from news.news_provider import DisabledCalendarProvider, GenericJsonCalendarProvider, TradingEconomicsProvider, JsonHeadlineProvider
from news.impact import analyze_release
from news.public_feeds import ForexFactoryProvider, OfficialRSSProvider


class NewsFilter:
    def __init__(self, app_cfg) -> None:
        self.cfg = app_cfg
        ncfg = app_cfg.get("news", {})
        env = app_cfg.env
        self.cache, self.last_success, self.last_poll = [], None, None
        self.last_error = None
        self.last_analysis = []
        timeout = float(ncfg.get("timeout_seconds", 10))
        hcfg = ncfg.get("headlines", {})
        self.headlines = None
        if hcfg.get("enabled", True) and env.news_api_url:
            self.headlines = JsonHeadlineProvider(env.news_api_url, env.news_api_key, timeout)
        elif hcfg.get("enabled", False):
            self.headlines = OfficialRSSProvider(hcfg.get("sources", []), env.news_cache_dir, timeout,
                                                int(hcfg.get("refresh_seconds", 300)))
        selected = ncfg.get("provider", "generic")
        if selected == "auto":
            selected = "generic" if env.economic_api_url else "forexfactory"
        if selected == "forexfactory":
            self.provider = ForexFactoryProvider(env.news_cache_dir, timeout, int(ncfg.get("calendar_refresh_seconds", 3600)))
            self.available = True
        elif selected == "tradingeconomics" and env.economic_api_key:
            self.provider = TradingEconomicsProvider(env.economic_api_key, timeout)
            self.available = True
        elif selected == "generic" and env.economic_api_url:
            self.provider = GenericJsonCalendarProvider(env.economic_api_url, env.economic_api_key, timeout)
            self.available = True
        else:
            self.provider = DisabledCalendarProvider(); self.available = False

    def currencies_for(self, symbol: str) -> set[str]:
        symbols = self.cfg.symbols.get("symbols", {})
        meta = symbols.get(symbol)
        if meta is None:
            matches = [m for k, m in symbols.items() if symbol in m.get("aliases", [k]) or symbol == m.get("broker_symbol")]
            if len(matches) != 1:
                return set()
            meta = matches[0]
        return set(meta.get("currencies", [])) | set(meta.get("extra_news_currencies", []))

    def refresh(self, now=None):
        ncfg = self.cfg.get("news", {})
        if not ncfg.get("enabled", True) or not self.available:
            return
        now = now or datetime.now(timezone.utc)
        before = int(ncfg.get("block_before_minutes", 30)); after = int(ncfg.get("block_after_minutes", 30))
        if self.last_poll is None or (now - self.last_poll).total_seconds() >= float(ncfg.get("poll_seconds", 60)):
            self.last_poll = now
            try:
                events = self.provider.events(now - timedelta(minutes=max(after, 120)), now + timedelta(days=1, minutes=before))
                if self.headlines:
                    events += self.headlines.events(now - timedelta(minutes=max(after, 120)), now)
                sources = [self.provider] + ([self.headlines] if self.headlines else [])
                if any(getattr(p, "source_updated_at", now) is not None and
                       (now - getattr(p, "source_updated_at", now)).total_seconds() > float(ncfg.get("stale_after_minutes", 60)) * 60 for p in sources):
                    raise ValueError("Stale source snapshot")
                self.cache, self.last_success, self.last_error = events, now, None
            except Exception as exc:
                # HTTP exception strings may contain URL credentials/API keys.
                self.last_error = type(exc).__name__
                logger.warning("News provider failure type={}", self.last_error)

    def evaluate(self, symbol: str, now=None) -> tuple[bool, str, float]:
        ncfg = self.cfg.get("news", {})
        self.last_analysis = []
        if not ncfg.get("enabled", True):
            return True, "News filtering disabled by config", 0.5
        if not self.available:
            if ncfg.get("fail_closed", True):
                return False, "Economic calendar unavailable (fail-closed)", 0.0
            return True, "Economic calendar unavailable (fail-open configured)", 0.0
        now = now or datetime.now(timezone.utc)
        currencies = self.currencies_for(symbol)
        if not currencies:
            return False, "News currency mapping unavailable", 0.0
        before = int(ncfg.get("block_before_minutes", 30)); after = int(ncfg.get("block_after_minutes", 30))
        self.refresh(now)
        if self.last_error or self.last_success is None or (now - self.last_success).total_seconds() > float(ncfg.get("stale_after_minutes", 60)) * 60:
            return (not ncfg.get("fail_closed", True), "News source failed/stale; new entries suspended" if ncfg.get("fail_closed", True) else "News unavailable (fail-open configured)", 0.0)
        if hasattr(self.provider, "covers") and not self.provider.covers(now - timedelta(minutes=after), now + timedelta(minutes=before)):
            return (not ncfg.get("fail_closed", True), "Calendar does not cover the complete news blocking window", 0.0)
        missing = currencies & getattr(self.headlines, "failed_currencies", set())
        if missing:
            return (not ncfg.get("fail_closed", True), f"Required official headline source unavailable for {','.join(sorted(missing))}", 0.0)
        relevant = [e for e in self.cache if e.currency in currencies and now - timedelta(minutes=after) <= e.timestamp <= now + timedelta(minutes=before)]
        self.last_analysis = [analyze_release(e, ncfg.get("surprise_rules", []), now) for e in relevant]
        blocked = [e for e in relevant if e.impact == "HIGH"]
        if blocked:
            nearest = min(blocked, key=lambda e: abs((e.timestamp-now).total_seconds()))
            mins = abs((nearest.timestamp-now).total_seconds()) / 60
            return False, f"High-impact {nearest.currency} event '{nearest.title}' source={nearest.source} within {mins:.0f}m; actual={nearest.actual} forecast={nearest.forecast} previous={nearest.previous} unit={nearest.unit} revision={nearest.revision}", 0.0
        uncertain = [a for e,a in zip(relevant,self.last_analysis) if e.kind == "release" and e.impact == "MEDIUM" and e.timestamp <= now and a["currency_bias"] is None]
        if uncertain and ncfg.get("block_unknown_effect", True):
            return False, f"Uncertain economic effect: {uncertain[0]['event']} ({uncertain[0]['source']})", 0.0
        return True, f"No mapped high-impact event in blocking window; polled={self.last_success.isoformat()}; directional effect unverified", 0.5

    def status(self):
        feed = getattr(self.provider, "feed", None)
        def stamp(name):
            value = getattr(self.provider, name, None)
            return value.isoformat() if value else None
        return {"enabled": bool(self.cfg.get("news.enabled", True)), "calendar_provider": type(self.provider).__name__,
                "calendar_configured": self.available, "last_poll": self.last_poll.isoformat() if self.last_poll else None,
                "calendar_ok": self.last_success is not None and self.last_error is None,
                "last_success": self.last_success.isoformat() if self.last_success else None,
                "last_error": self.last_error, "cached_events": len(self.cache),
                "calendar_checked_at": stamp("source_updated_at"),
                "calendar_events_loaded": len(getattr(self.provider, "all_events", [])),
                "calendar_rows_downloaded": getattr(self.provider, "raw_count", None),
                "calendar_http_status": feed.metadata.get("http_status") if feed else None,
                "calendar_error": feed.metadata.get("error") if feed else None,
                "calendar_next_request_at": feed.metadata.get("retry_at") if feed else None,
                "calendar_coverage_start": stamp("coverage_start"),
                "calendar_coverage_end": stamp("coverage_end"),
                "headlines": getattr(self.headlines, "statuses", []),
                "unavailable_headline_currencies": sorted(getattr(self.headlines, "failed_currencies", set()))}

    def direction_allowed(self, symbol, direction):
        meta = self.cfg.symbols.get("symbols", {}).get(symbol, {})
        pair = meta.get("currencies", [])
        if len(pair) != 2:
            return True
        biases = [a["currency_bias"] * (1 if a["currency"] == pair[0] else -1) for a in self.last_analysis if a["currency_bias"] is not None and a["currency"] in pair]
        if any(x > 0 for x in biases) and any(x < 0 for x in biases):
            return False
        return not any((x < 0 if direction == "BUY" else x > 0) for x in biases)
