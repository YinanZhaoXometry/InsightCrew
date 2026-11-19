"""Polymarket 适配器的测试（BLUEPRINT 19.18 WP30）：解析保存的响应（手工构造的夹具）
成 19.8 的 markets 归档形状、只留开放且前瞻的市场、按成交额排序、上限条数，
空结果与失败抛类型化错误。全部离线（MockTransport 重放夹具）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.polymarket import download_markets
from insightcrew.domain.market import NoData, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
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


# ---- 解析成 19.8 的 markets 归档形状 ----


def test_keeps_open_forward_looking_markets_ranked_by_volume() -> None:
    replay = Replay("search_fed.json")
    with client(replay) as c:
        content = download_markets(c, "Fed rate cut", today=TODAY)
    assert content == {
        "source": "Polymarket",
        "fetched_on": "2026-11-19",
        "markets": [
            {"question": "Will the Fed cut rates at the December 2026 meeting?",
             "probability": 0.76, "volume": 1254300.5, "end_date": "2026-12-16"},
            {"question": "Will the Fed cut 50bps at the December 2026 meeting?",
             "probability": 0.08, "volume": 312000.0, "end_date": "2026-12-16"},
            {"question": "US recession announced by March 2027?",
             "probability": 0.18, "volume": 95400.0, "end_date": "2027-03-31"},
        ],
    }
    # 已结算的（closed，成交额最大也不算）、截止日已过的、outcomePrices 解析不出来的都不在


def test_the_limit_keeps_the_most_traded_markets() -> None:
    with client(Replay("search_fed.json")) as c:
        content = download_markets(c, "Fed rate cut", today=TODAY, limit=2)
    assert [market["probability"] for market in content["markets"]] == [0.76, 0.08]


def test_the_search_endpoint_receives_the_query() -> None:
    replay = Replay("search_fed.json")
    with client(replay) as c:
        download_markets(c, "Fed rate cut", today=TODAY)
    assert replay.urls == ["https://gamma-api.polymarket.com/public-search"
                           "?q=Fed%20rate%20cut&limit_per_type=20"]


def test_no_matching_markets_is_no_data() -> None:
    with client(Replay("empty.json")) as c, pytest.raises(NoData, match="open interest"):
        download_markets(c, "open interest", today=TODAY)


def test_a_refused_response_raises_with_the_status() -> None:
    with client(Replay("search_fed.json", status=502)) as c, \
            pytest.raises(SourceUnavailable, match="502"):
        download_markets(c, "Fed rate cut", today=TODAY)


def test_a_malformed_body_raises_source_unavailable() -> None:
    with client(Replay("search_fed.json", raw="<html>not json</html>")) as c, \
            pytest.raises(SourceUnavailable, match="不是 JSON"):
        download_markets(c, "Fed rate cut", today=TODAY)
