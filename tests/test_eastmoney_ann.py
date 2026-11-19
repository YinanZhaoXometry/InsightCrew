"""东方财富公告适配器的测试（BLUEPRINT 18.13）：分页与停止条件、五位代码、
covers_from 的取法、失败抛 SourceUnavailable。全部离线（MockTransport 重放保存的响应）。"""
from __future__ import annotations

import datetime
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.eastmoney_ann import download_announcements
from insightcrew.domain.market import SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "eastmoney_ann"
TODAY = datetime.date(2026, 11, 19)


class Pages:
    """按页码返回保存的响应，并记录请求过的 URL。"""

    def __init__(self, pages: dict[int, Path], status: int = 200):
        self.pages, self.status, self.urls = pages, status, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.urls.append(str(request.url))
        number = int(str(request.url).partition("page_index=")[2].partition("&")[0])
        path = self.pages.get(number)
        if path is None:
            return httpx2.Response(200, text='{"data": {"list": []}}')
        return httpx2.Response(self.status, text=path.read_text(encoding="utf-8"))


def client(pages: Pages) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(pages))


def two_pages() -> Pages:
    return Pages({1: FIXTURES / "page_1.json", 2: FIXTURES / "page_2.json"})


def test_pagination_reads_until_a_date_before_the_start() -> None:
    pages = two_pages()
    with client(pages) as c:
        ticker, content = download_announcements(c, "0700.HK", datetime.date(2025, 1, 1), today=TODAY)
    assert ticker == "0700.HK"
    assert len(pages.urls) == 2                                   # 第二页出现早于起点的日期后停止
    assert [item["ref"] for item in content["items"]] == [
        "AP6900099", "AP6900098", "AP6900097", "AP6900096", "AP6900095"]
    assert content["covers_from"] == "2025-01-01"                 # 翻到了请求的起点
    assert content["covers_to"] == "2026-11-19"
    assert content["source"] == "东方财富"


def test_a_start_inside_the_first_page_stops_there() -> None:
    pages = two_pages()
    with client(pages) as c:
        _, content = download_announcements(c, "0700.HK", datetime.date(2026, 1, 1), today=TODAY)
    assert len(pages.urls) == 1                                   # 第一页已有更早的日期
    assert [item["published"] for item in content["items"]] == [
        "2026-08-27", "2026-03-26", "2025-08-28"]
    assert content["covers_from"] == "2026-01-01"


def test_a_window_beyond_the_pages_reports_the_oldest_date_read() -> None:
    pages = two_pages()
    with client(pages) as c:
        _, content = download_announcements(c, "0700.HK", datetime.date(2020, 1, 1), today=TODAY)
    assert content["covers_from"] == "2024-09-30"                 # 没翻到 2020：覆盖只能说到最老一条


def test_the_five_digit_code_is_used_in_the_url() -> None:
    pages = two_pages()
    with client(pages) as c:
        download_announcements(c, "0700.HK", datetime.date(2026, 1, 1), today=TODAY)
    assert "stock_list=00700" in pages.urls[0]
    assert "page_size=50" in pages.urls[0] and "ann_type=H" in pages.urls[0]


def test_notice_date_uses_its_first_ten_characters() -> None:
    with client(two_pages()) as c:
        _, content = download_announcements(c, "0700.HK", datetime.date(2026, 1, 1), today=TODAY)
    assert content["items"][0]["published"] == "2026-08-27"       # 源数据是 "2026-08-27 16:30"
    assert content["items"][0]["kind"] == "中期业绩"
    assert content["items"][0]["title"] == "2026 年中期业绩公告"


def test_an_empty_page_stops_the_paging() -> None:
    pages = Pages({1: FIXTURES / "empty.json"})
    with client(pages) as c:
        _, content = download_announcements(c, "0700.HK", datetime.date(2025, 1, 1), today=TODAY)
    assert content["items"] == []
    assert len(pages.urls) == 1


def test_a_refused_response_raises_with_the_status() -> None:
    pages = Pages({1: FIXTURES / "page_1.json"}, status=502)
    with client(pages) as c, pytest.raises(SourceUnavailable, match="502"):
        download_announcements(c, "0700.HK", datetime.date(2025, 1, 1), today=TODAY)
