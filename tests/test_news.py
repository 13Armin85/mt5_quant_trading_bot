from config import AppConfig, EnvSettings
from news.news_filter import NewsFilter
from news.news_provider import NewsEvent, GenericJsonCalendarProvider, TradingEconomicsProvider
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
import pytest


def config():
    return AppConfig(settings={"news":{"enabled":True,"fail_closed":True,"poll_seconds":60}},symbols={"symbols":{"EURUSD":{"aliases":["EURUSDm"],"currencies":["EUR","USD"]}}},strategy={},env=EnvSettings(_env_file=None,ECONOMIC_API_URL="https://calendar.invalid"))


class Provider:
    def __init__(self, events): self.rows, self.calls = events, 0
    def events(self, start, end): self.calls += 1; return self.rows


def test_news_fail_closed_without_provider():
    cfg=AppConfig(settings={"news":{"enabled":True,"fail_closed":True}},symbols={"symbols":{"EURUSD":{"currencies":["EUR","USD"]}}},strategy={},env=EnvSettings())
    ok,reason,score=NewsFilter(cfg).evaluate("EURUSD")
    assert not ok and score==0


@pytest.mark.parametrize("currency", ["EUR","USD"])
def test_both_currencies_block_and_alias_mapping(currency):
    now = datetime.now(timezone.utc)
    nf = NewsFilter(config())
    nf.provider = Provider([NewsEvent(now+timedelta(minutes=10),currency,"HIGH","CPI")])
    assert not nf.evaluate("EURUSDm", now)[0]


def test_events_outside_window_do_not_block_and_cache_rate_limit():
    now = datetime.now(timezone.utc)
    nf = NewsFilter(config())
    nf.provider = Provider([NewsEvent(now+timedelta(minutes=90),"USD","HIGH","CPI")])
    assert nf.evaluate("EURUSD",now)[0]
    assert nf.evaluate("EURUSD",now+timedelta(seconds=10))[0]
    assert nf.provider.calls == 1


def test_provider_failure_after_success_blocks(monkeypatch):
    now = datetime.now(timezone.utc)
    nf = NewsFilter(config()); nf.provider = Provider([])
    assert nf.evaluate("EURUSD",now)[0]
    def fail(*args): raise httpx.ReadTimeout("secret API URL must not appear in decision")
    monkeypatch.setattr(nf.provider,"events",fail)
    ok, reason, _ = nf.evaluate("EURUSD",now+timedelta(seconds=61))
    assert not ok and "secret" not in reason


def test_stale_source_blocks():
    now = datetime.now(timezone.utc)
    nf = NewsFilter(config()); nf.provider = Provider([])
    nf.provider.source_updated_at = now - timedelta(hours=2)
    assert not nf.evaluate("EURUSD",now)[0]


def test_generic_provider_deduplicates_revision(monkeypatch):
    rows = [{"id":"1","timestamp":"2026-01-01T12:00:00Z","currency":"USD","title":"CPI","impact":"HIGH","actual":"2","updated_at":"2026-01-01T12:00:00Z"},
            {"id":"1","timestamp":"2026-01-01T12:00:00Z","currency":"USD","title":"CPI","impact":"HIGH","actual":"3","updated_at":"2026-01-01T12:01:00Z"}]
    response = httpx.Response(200,json={"events":rows},request=httpx.Request("GET","https://test.invalid"))
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k:response)
    provider = GenericJsonCalendarProvider("https://test.invalid")
    events = provider.events(datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert len(events)==1 and events[0].actual=="3" and events[0].received_at.tzinfo


def test_naive_timestamp_fails_closed(monkeypatch):
    response = httpx.Response(200,json=[{"timestamp":"2026-01-01T12:00:00","currency":"USD","title":"CPI","impact":"HIGH"}],request=httpx.Request("GET","https://test.invalid"))
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k:response)
    with pytest.raises(ValueError,match="timezone"):
        GenericJsonCalendarProvider("https://test.invalid").events(datetime.now(timezone.utc),datetime.now(timezone.utc))


def test_te_country_mapping_when_currency_is_empty(monkeypatch):
    response=httpx.Response(200,json=[{"CalendarId":"1","Date":"2026-01-01T12:00:00","Country":"United States","Currency":"","Importance":3,"Event":"Payrolls"}],request=httpx.Request("GET","https://test.invalid"))
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k:response)
    events=TradingEconomicsProvider("fake-test-key").events(datetime.now(timezone.utc),datetime.now(timezone.utc))
    assert events[0].currency=="USD" and events[0].impact=="HIGH"
