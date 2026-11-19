"""StockTwits 适配器的测试（BLUEPRINT 19.18 WP30）：解析保存的响应（手工构造的夹具）
成 19.8 的 social 归档形状、HTML 反转义、多空标签、固定链接，空流与失败抛类型化错误。
全部离线（MockTransport 重放夹具）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.stocktwits import download_posts
from insightcrew.domain.market import NoData, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "stocktwits"
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


def test_parses_messages_into_the_archive_shape() -> None:
    replay = Replay("aapl.json")
    with client(replay) as c:
        content = download_posts(c, "AAPL", today=TODAY)
    assert content == {
        "source": "StockTwits",
        "covers_from": "2026-11-19",
        "covers_to": "2026-11-19",
        "posts": [
            {"published": "2026-11-19", "source": "@macro_bull",
             "text": "$AAPL breakouts & new highs — add > trim later",   # HTML 已反转义
             "url": "https://stocktwits.com/macro_bull/message/512344001",
             "tag": "Bullish"},
            {"published": "2026-11-19", "source": "@tape_reader",
             "text": "Chart looks heavy at these levels, fading the pop",
             "url": "https://stocktwits.com/tape_reader/message/512344002",
             "tag": "Bearish"},
            {"published": "2026-11-18", "source": "@long_only_lou",
             "text": "Holding thru earnings '27, not trading this noise",
             "url": "https://stocktwits.com/long_only_lou/message/512343999",
             "tag": None},                                              # 没有自标标签：null
        ],
    }


def test_the_symbol_is_uppercased_in_the_url() -> None:
    replay = Replay("aapl.json")
    with client(replay) as c:
        download_posts(c, "aapl", today=TODAY)
    assert replay.urls == ["https://api.stocktwits.com/api/2/streams/symbol/AAPL.json"]


def test_hk_tickers_pass_through_unchanged() -> None:
    replay = Replay("aapl.json")
    with client(replay) as c:
        download_posts(c, "0700.HK", today=TODAY)
    assert "0700.HK" in replay.urls[0]


def test_an_empty_stream_raises_no_data() -> None:
    with client(Replay("empty.json")) as c, pytest.raises(NoData, match=r"0700\.HK"):
        download_posts(c, "0700.HK", today=TODAY)


def test_a_throttled_response_raises_with_the_status() -> None:
    with client(Replay("aapl.json", status=429)) as c, \
            pytest.raises(SourceUnavailable, match="429"):
        download_posts(c, "AAPL", today=TODAY)


def test_a_malformed_body_raises_source_unavailable() -> None:
    with client(Replay("aapl.json", raw="<html>not json</html>")) as c, \
            pytest.raises(SourceUnavailable, match="不是 JSON"):
        download_posts(c, "AAPL", today=TODAY)
