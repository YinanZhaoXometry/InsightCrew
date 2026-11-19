"""Reddit Atom 搜索源适配器的测试（BLUEPRINT 19.18 WP30）：解析保存的响应（手工构造的
夹具）成 19.8 的 social 归档形状、按 category 分版块、正文的 HTML 剥离与两层反转义、
合并版块的查询串，空源与失败抛类型化错误。全部离线（MockTransport 重放夹具）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.reddit import download_posts
from insightcrew.domain.market import NoData, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "reddit"
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


# ---- 解析成 19.8 的 social 归档形状 ----


def test_parses_the_atom_feed_into_the_archive_shape() -> None:
    replay = Replay("aapl.rss")
    with client(replay) as c:
        content = download_posts(c, "AAPL", today=TODAY)
    assert content == {
        "source": "Reddit",
        "covers_from": "2026-11-19",
        "covers_to": "2026-11-19",
        "posts": [
            {"published": "2026-11-19", "source": "r/stocks",
             "text": "AAPL what are your positions on the new Macs?\n"
                     "M5 looks like a genuine leap, but the pricing's steep. Anyone adding?",
             "url": "https://www.reddit.com/r/stocks/comments/1k2m3n4/"
                    "aapl_what_are_your_positions_on_the_new_macs/",
             "tag": None},
            {"published": "2026-11-19", "source": "r/wallstreetbets",
             "text": "Daily Discussion Thread - November 19, 2026\nYour daily trading discussion thread.",
             "url": "https://www.reddit.com/r/wallstreetbets/comments/1k2m3n5/"
                    "daily_discussion_thread_november_19_2026/",
             "tag": None},
            {"published": "2026-11-18", "source": "r/investing",
             "text": "Is the rally overextended?\nMetric Value",   # 没有 SC_OFF：整段剥标签
             "url": "https://www.reddit.com/r/investing/comments/1k2m3n6/is_the_rally_overextended/",
             "tag": None},
        ],
    }


def test_the_subreddits_are_combined_into_one_request() -> None:
    replay = Replay("aapl.rss")
    with client(replay) as c:
        download_posts(c, "AAPL", today=TODAY)
    assert len(replay.urls) == 1
    url = replay.urls[0]
    assert url.startswith("https://www.reddit.com/r/wallstreetbets+stocks+investing/search.rss?")
    for part in ("q=AAPL", "restrict_sr=on", "sort=new", "t=week", "limit=100"):
        assert part in url


def test_a_single_subreddit_labels_unlabelled_entries() -> None:
    """单一版块的源可能不给 category：那样的条目记那个版块。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text=(
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>One title</title>"
            '<link href="https://www.reddit.com/r/stocks/comments/1/x/"/>'
            "<published>2026-11-19T09:00:00+00:00</published></entry></feed>"))

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as c:
        content = download_posts(c, "AAPL", subreddits=("stocks",), today=TODAY)
    assert content["posts"][0]["source"] == "r/stocks"
    assert content["posts"][0]["text"] == "One title"


def test_an_empty_feed_raises_no_data() -> None:
    with client(Replay("empty.rss")) as c, pytest.raises(NoData, match="ZZZZ"):
        download_posts(c, "ZZZZ", today=TODAY)


def test_a_refused_response_raises_with_the_status() -> None:
    with client(Replay("aapl.rss", status=429)) as c, \
            pytest.raises(SourceUnavailable, match="429"):
        download_posts(c, "AAPL", today=TODAY)


def test_a_malformed_body_raises_source_unavailable() -> None:
    with client(Replay("aapl.rss", raw="<not-xml")) as c, \
            pytest.raises(SourceUnavailable, match="不是 Atom/XML"):
        download_posts(c, "AAPL", today=TODAY)
