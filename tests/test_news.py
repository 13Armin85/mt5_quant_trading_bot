from config import AppConfig, EnvSettings
from news.news_filter import NewsFilter


def test_news_fail_closed_without_provider():
    cfg=AppConfig(settings={"news":{"enabled":True,"fail_closed":True}},symbols={"symbols":{"EURUSD":{"currencies":["EUR","USD"]}}},strategy={},env=EnvSettings())
    ok,reason,score=NewsFilter(cfg).evaluate("EURUSD")
    assert not ok and score==0
