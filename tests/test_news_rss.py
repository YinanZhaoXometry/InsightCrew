"""Google News RSS 与 Yahoo Finance RSS 适配器的测试（BLUEPRINT 19.18 WP30）：
解析保存的响应（手工构造的夹具）成 19.8 的 news 归档形状、条目级 source 与 published 的
缺省、空源与失败抛类型化错误。全部离线（MockTransport 重放夹具）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.news_rss import download_google, download_yahoo
from insightcrew.domain.market import NoData, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "news_rss"
TODAY = datetime.date(2026, 11, 19)


class Replay:
    """给任何请求回同一份夹具文本，并记录请求过的 URL 与状态选择。"""

    def __init__(self, name: str, status: int = 200, raw: str | None = None):
        self.name, self.status, self.raw, self.urls = name, status, raw, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.urls.append(str(request.url))
        text = self.raw if self.raw is not None else (FIXTURES / self.name).read_text(encoding="utf-8")
        return httpx2.Response(self.status, text=text)


def client(replay: Replay) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(replay))


# ---- 解析成 19.8 的 news 归档形状 ----


def test_google_parses_the_feed_into_the_archive_shape() -> None:
    replay = Replay("google_aapl.xml")
    with client(replay) as c:
        content = download_google(c, "AAPL", today=TODAY)
    assert content == {
        "source": "Google News RSS",
        "covers_from": "2026-11-19",
        "covers_to": "2026-11-19",
        "items": [
            {"published": "2026-11-19", "source": "Bloomberg",
             "title": "Apple unveils M5 MacBook Pro lineup - Bloomberg",
             "url": "https://news.google.com/rss/articles/CBMi1GVtNWJvb2twcm8?oc=5"},
            {"published": "2026-11-18", "source": "Reuters",
             "title": "AAPL closes at a record high as analysts raise targets - Reuters",
             "url": "https://news.google.com/rss/articles/CMi2cmV1dGVyc2NoZWNr?oc=5"},
            {"published": "2026-11-17", "source": "Google News",      # 没有 <source> 元素：用源名
             "title": "Apple supplier Foxconn reports strong monthly sales",
             "url": "https://news.google.com/rss/articles/CMi3Zm94Y29ubmVjdA?oc=5"},
            {"published": None, "source": "MacRumors",                # 没有 pubDate：存 null
             "title": "Weekend read: what the M5 means for the iPad roadmap",
             "url": "https://news.google.com/rss/articles/CMi4aXBhZHJvYWRtYXA?oc=5"},
        ],
    }


def test_yahoo_parses_the_feed_into_the_archive_shape() -> None:
    replay = Replay("yahoo_aapl.xml")
    with client(replay) as c:
        content = download_yahoo(c, "AAPL", today=TODAY)
    assert content == {
        "source": "Yahoo Finance RSS",
        "covers_from": "2026-11-19",
        "covers_to": "2026-11-19",
        "items": [
            {"published": "2026-11-19", "source": "Yahoo Finance",
             "title": "Apple wins patent case over wearable health sensors",
             "url": "https://finance.yahoo.com/news/apple-wins-patent-case-123000123.html"},
            {"published": "2026-11-18", "source": "Yahoo Finance",
             "title": "Why Apple's services segment keeps surprising Wall Street",
             "url": "https://finance.yahoo.com/news/why-apple-services-keeps-surprising-090000567.html"},
        ],
    }


def test_the_queries_reach_the_right_endpoints() -> None:
    google, yahoo = Replay("google_aapl.xml"), Replay("yahoo_aapl.xml")
    with client(google) as c:
        download_google(c, "AAPL", today=TODAY)
    with client(yahoo) as c:
        download_yahoo(c, "AAPL", today=TODAY)
    assert google.urls == ["https://news.google.com/rss/search?q=AAPL&hl=en-US&gl=US&ceid=US:en"]
    assert yahoo.urls == ["https://feeds.finance.yahoo.com/rss/2.0/headline"
                          "?s=AAPL&region=US&lang=en-US"]


def test_an_empty_feed_raises_no_data() -> None:
    with client(Replay("google_empty.xml")) as c, pytest.raises(NoData, match="AAPL"):
        download_google(c, "AAPL", today=TODAY)


def test_a_refused_response_raises_with_the_status() -> None:
    with client(Replay("google_aapl.xml", status=403)) as c, \
            pytest.raises(SourceUnavailable, match="403"):
        download_google(c, "AAPL", today=TODAY)


def test_a_malformed_body_raises_source_unavailable() -> None:
    with client(Replay("google_aapl.xml", raw="<html>not a feed")) as c, \
            pytest.raises(SourceUnavailable, match="不是 RSS/XML"):
        download_google(c, "AAPL", today=TODAY)
