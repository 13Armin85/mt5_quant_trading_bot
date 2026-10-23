import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from config import AppConfig, EnvSettings, load_config
from news.impact import analyze_release
from news.news_filter import NewsFilter
from news.news_provider import NewsEvent
from news.public_feeds import CachedPublicFeed, ForexFactoryProvider, OfficialRSSProvider, FeedUnavailable


UTC = timezone.utc


def respond(monkeypatch, content, status=200, headers=None):
    calls = []
    def get(_, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, content=content, headers=headers,
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.Client, "get", get)
    return calls


def source(currency="USD", **kwargs):
    return {"name": "Official Test", "url": "https://official.test/feed", "currencies": [currency],
            "required": True, "default_impact": "LOW", "high_impact_keywords": ["interest rate"], **kwargs}


def rss(title="Interest rate decision", published=None):
    stamp = format_datetime(published or datetime.now(UTC) - timedelta(minutes=10))
    return f'<rss version="2.0"><channel><item><title>{title}</title><link>https://official.test/decision</link><guid>one</guid><pubDate>{stamp}</pubDate></item></channel></rss>'.encode()


def calendar_row(**kwargs):
    return {"title": "CPI", "country": "USD", "date": datetime.now(UTC).isoformat(),
            "impact": "High", "forecast": "2.1%", "previous": "2.0%", **kwargs}


def test_weekly_calendar_parses_currency_impact_and_no_fabricated_actuals(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    respond(monkeypatch, json.dumps([calendar_row(), calendar_row(title="Holiday", impact="Holiday")]).encode())
    provider = ForexFactoryProvider(tmp_path)
    events = provider.events(now - timedelta(hours=1), now + timedelta(hours=1))
    assert len(events) == 1 and events[0].impact == "HIGH" and events[0].currency == "USD"
    assert events[0].forecast == "2.1%" and events[0].actual is None
    assert provider.covers(now, now)
    assert not provider.covers(provider.coverage_end - timedelta(minutes=10), provider.coverage_end + timedelta(minutes=10))
    assert provider.coverage_start.astimezone(ZoneInfo("America/New_York")).weekday() == 6


@pytest.mark.parametrize("change", [{"impact": "Unknown"}, {"date": "2026-01-01T12:00:00"}, {"country": "UNKNOWN"}, {"title": ""}])
def test_invalid_weekly_event_fails_closed(tmp_path, monkeypatch, change):
    respond(monkeypatch, json.dumps([calendar_row(**change)]).encode())
    with pytest.raises(ValueError):
        ForexFactoryProvider(tmp_path).events(datetime.now(UTC), datetime.now(UTC))


def test_old_calendar_week_is_not_silently_accepted(tmp_path, monkeypatch):
    respond(monkeypatch, json.dumps([calendar_row(date="2020-01-01T12:00:00Z")]).encode())
    with pytest.raises(FeedUnavailable, match="current date"):
        ForexFactoryProvider(tmp_path).events(datetime.now(UTC), datetime.now(UTC))


def test_global_calendar_events_map_to_major_currencies(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    respond(monkeypatch, json.dumps([calendar_row(country="All")]).encode())
    events = ForexFactoryProvider(tmp_path).events(now - timedelta(hours=1), now + timedelta(hours=1))
    assert {e.currency for e in events} >= {"USD", "EUR", "GBP", "JPY"}


def test_cache_survives_process_objects_and_respects_download_interval(tmp_path, monkeypatch):
    calls = respond(monkeypatch, b'{"data":true}', headers={"ETag": '"version-one"'})
    first = CachedPublicFeed("https://official.test/feed", tmp_path, interval=3600)
    assert first.content() == b'{"data":true}'
    assert CachedPublicFeed(first.url, tmp_path).content() == b'{"data":true}'
    assert len(calls) == 1
    state = first._read(); state["retry_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(); first._write(state)
    calls = respond(monkeypatch, b"", status=304)
    assert first.content() == b'{"data":true}'
    assert calls[0][1]["headers"]["If-None-Match"] == '"version-one"'


def test_rate_limit_has_persistent_backoff_and_never_counts_as_empty_success(tmp_path, monkeypatch):
    calls = respond(monkeypatch, b"rate limited", status=429, headers={"Retry-After": "7200"})
    first = CachedPublicFeed("https://official.test/feed", tmp_path)
    with pytest.raises(FeedUnavailable):
        first.content()
    second = CachedPublicFeed(first.url, tmp_path)
    with pytest.raises(FeedUnavailable):
        second.content()
    assert len(calls) == 1
    assert datetime.fromisoformat(second.metadata["retry_at"]) > datetime.now(UTC) + timedelta(minutes=119)


def test_rss_publication_time_link_currency_and_transparent_risk_flag():
    events = OfficialRSSProvider.parse(rss(), source())
    assert len(events) == 1 and events[0].kind == "headline" and events[0].impact == "HIGH"
    assert events[0].published_at.tzinfo and events[0].url == "https://official.test/decision"
    assert events[0].currency == "USD"
    events[0].actual, events[0].forecast = "10", "1"
    result = analyze_release(events[0], [{"contains": "rate", "higher_is_positive": True}], datetime.now(UTC))
    assert result["currency_bias"] is None  # A headline cannot become a buy/sell rule.


def test_atom_and_rdf_dates_parse_as_utc():
    date = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    atom = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Policy</title><id>two</id><link href="https://official.test/two"/><updated>{date}</updated></entry></feed>'
    rdf = f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/"><item><title>Policy</title><dc:date>{date}</dc:date></item></rdf:RDF>'
    assert OfficialRSSProvider.parse(atom.encode(), source())[0].url.endswith("/two")
    assert OfficialRSSProvider.parse(rdf.encode(), source())[0].timestamp.tzinfo


@pytest.mark.parametrize("content", [b"<html>Blocked</html>", b"<rss><channel/></rss>", b"<!DOCTYPE rss [<!ENTITY a 'unsafe'>]><rss/>", '<!DOCTYPE rss [<!ENTITY a "unsafe">]><rss/>'.encode("utf-16")])
def test_invalid_or_entity_xml_is_never_an_empty_success(content):
    with pytest.raises(ValueError):
        OfficialRSSProvider.parse(content, source())


def test_rss_failure_keeps_other_publishers_and_marks_only_affected_currency(tmp_path, monkeypatch):
    def get(_, url, **kwargs):
        status = 403 if "blocked" in url else 200
        return httpx.Response(status, content=rss(), request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.Client, "get", get)
    feeds = OfficialRSSProvider([source(), source("EUR", name="Blocked ECB", url="https://blocked.test/feed")], tmp_path)
    now = datetime.now(UTC)
    events = feeds.events(now - timedelta(hours=1), now)
    assert len(events) == 1 and events[0].currency == "USD"
    assert feeds.failed_currencies == {"EUR"}
    assert feeds.statuses[0]["ok"] and feeds.statuses[1]["http_status"] == 403


def test_default_config_uses_public_calendar_without_keys(tmp_path):
    cfg = load_config().model_copy(deep=True)
    cfg.env = EnvSettings(_env_file=None, NEWS_CACHE_DIR=str(tmp_path))
    cfg.validate_runtime()
    news = NewsFilter(cfg)
    assert isinstance(news.provider, ForexFactoryProvider)
    assert isinstance(news.headlines, OfficialRSSProvider)


def test_explicit_custom_calendar_url_is_preserved():
    cfg = AppConfig(settings={"news": {"provider": "auto"}}, symbols={}, strategy={},
                    env=EnvSettings(_env_file=None, ECONOMIC_API_URL="https://authorized.test/calendar"))
    assert type(NewsFilter(cfg).provider).__name__ == "GenericJsonCalendarProvider"


def test_required_rss_outage_blocks_relevant_currency_without_erasing_calendar(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    respond(monkeypatch, b"blocked", status=403)
    cfg = AppConfig(settings={"news": {"enabled": True, "provider": "generic", "fail_closed": True,
                                      "headlines": {"enabled": True, "sources": [source("EUR")]}}},
                    symbols={"symbols": {"USDJPY": {"currencies": ["USD", "JPY"]}, "EURUSD": {"currencies": ["EUR", "USD"]}}},
                    strategy={}, env=EnvSettings(_env_file=None, ECONOMIC_API_URL="https://calendar.test", NEWS_CACHE_DIR=str(tmp_path)))
    news = NewsFilter(cfg)
    class Calendar:
        def events(self, *args): return []
    news.provider = Calendar()
    assert news.evaluate("USDJPY", now)[0]
    allowed, reason, _ = news.evaluate("EURUSD", now)
    assert not allowed and "EUR" in reason


def test_headlines_are_considered_for_gold_usd_exposure(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    respond(monkeypatch, rss())
    cfg = AppConfig(settings={"news": {"enabled": True, "provider": "generic", "fail_closed": True,
                                      "headlines": {"enabled": True, "sources": [source()]}}},
                    symbols={"symbols": {"XAUUSD": {"currencies": ["XAU", "USD"]}}}, strategy={},
                    env=EnvSettings(_env_file=None, ECONOMIC_API_URL="https://calendar.test", NEWS_CACHE_DIR=str(tmp_path)))
    news = NewsFilter(cfg)
    class Calendar:
        def events(self, *args): return []
    news.provider = Calendar()
    allowed, reason, _ = news.evaluate("XAUUSD", now)
    assert not allowed and "Interest rate decision" in reason


def test_stale_http_cache_blocks_required_rss_currency(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    respond(monkeypatch, rss(), headers={"Age": "10000"})
    feed = OfficialRSSProvider([source()], tmp_path)
    assert not feed.events(now - timedelta(hours=1), now)
    assert feed.failed_currencies == {"USD"} and not feed.statuses[0]["ok"]


def test_news_cli_never_initializes_mt5_or_builds_execution_runtime(tmp_path, monkeypatch, capsys):
    import main
    output = tmp_path / "news.json"
    def forbidden(*args, **kwargs):
        raise AssertionError("A news diagnostic must not construct an MT5 runtime")
    class ReadOnlyNews:
        cache, headlines = [], None
        def __init__(self, cfg): pass
        def evaluate(self, symbol, now): return False, "Scheduled news block", 0
        def currencies_for(self, symbol): return {"USD"}
        def status(self): return {"calendar_ok": True}
    monkeypatch.setattr(main, "NewsFilter", ReadOnlyNews)
    monkeypatch.setattr(main, "create_mt5_client", forbidden)
    monkeypatch.setattr(main, "build_runtime", forbidden)
    monkeypatch.setattr("sys.argv", ["main.py", "news", "--symbol", "XAUUSD", "--output", str(output)])
    main.main()
    result = json.loads(output.read_text())
    assert result["read_only"] and not result["orders_enabled"]
    assert not result["news_allows_entry"] and result["sources"]["calendar_ok"]
