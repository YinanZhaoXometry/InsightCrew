"""FRED 适配器的测试（BLUEPRINT 19.18 WP30）：别名到序列 ID、vintage 钉住的请求参数、
解析保存的响应（手工构造的夹具）成 19.8 的 macro 归档形状、没有密钥一个请求都不发
（NotConfigured）、400 是 NotCovered、错误消息绝不带密钥。全部离线（MockTransport）。"""
from __future__ import annotations

import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from insightcrew.adapters.fred import download_series
from insightcrew.domain.market import NoData, NotConfigured, NotCovered, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "fred"
KEY = "test-key-123"
AS_OF = datetime.date(2026, 11, 19)


class Series:
    """按 series_id 回对应的夹具：认识的序列 200，不认识 400 带 FRED 的 error_message。"""

    def __init__(self, status: int | None = None):
        self.status, self.urls = status, []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.urls.append(str(request.url))
        series_id = parse_qs(urlsplit(str(request.url)).query)["series_id"][0]
        if self.status is None and series_id != "ZZZZZZ":
            return httpx2.Response(
                200, text=(FIXTURES / f"observations_{series_id.lower()}.json").read_text(encoding="utf-8"))
        status = self.status if self.status is not None else 400
        return httpx2.Response(status, text=(FIXTURES / "error_unknown_series.json").read_text(encoding="utf-8"))


def client(series: Series) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(series))


@pytest.fixture
def fred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_FRED_API_KEY", KEY)


# ---- 没有密钥 ----


def test_without_a_key_no_request_is_made_and_not_configured_is_raised(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IC_FRED_API_KEY", raising=False)
    series = Series()
    with client(series) as c, pytest.raises(NotConfigured, match="IC_FRED_API_KEY"):
        download_series(c, "cpi", as_of=AS_OF)
    assert series.urls == []                                        # 一个请求都没发


# ---- 请求参数：别名与 vintage 钉住 ----


def test_the_alias_is_mapped_and_the_vintage_is_pinned_to_as_of(fred_key: None) -> None:
    series = Series()
    with client(series) as c:
        download_series(c, "cpi", as_of=AS_OF)
    query = parse_qs(urlsplit(series.urls[0]).query)
    assert series.urls[0].startswith("https://api.stlouisfed.org/fred/series/observations?")
    assert query["series_id"] == ["CPIAUCSL"]
    assert query["realtime_start"] == ["2026-11-19"]                # vintage 钉在分析日期
    assert query["realtime_end"] == ["2026-11-19"]
    assert query["observation_end"] == ["2026-11-19"]
    assert query["observation_start"] == ["2025-11-19"]             # 缺省回看一年
    assert query["api_key"] == [KEY]
    assert query["file_type"] == ["json"]


# ---- 解析成 19.8 的 macro 归档形状 ----


def test_observations_are_parsed_into_the_archive_shape(fred_key: None) -> None:
    with client(Series()) as c:
        content = download_series(c, "cpi", as_of=AS_OF)
    assert content == {
        "source": "FRED",
        "alias": "cpi",
        "fred_id": "CPIAUCSL",
        "observations": [
            {"date": "2025-11-01", "value": 322.5, "realtime_start": "2025-12-10"},
            {"date": "2025-12-01", "value": 323.1, "realtime_start": "2026-01-13"},
            {"date": "2026-01-01", "value": 324.391, "realtime_start": "2026-02-11"},
            {"date": "2026-02-01", "value": 325.586, "realtime_start": "2026-03-11"},
            {"date": "2026-03-01", "value": 326.603, "realtime_start": "2026-04-10"},
            {"date": "2026-04-01", "value": 327.171, "realtime_start": "2026-05-13"},
            {"date": "2026-05-01", "value": 328.12, "realtime_start": "2026-06-11"},
            {"date": "2026-06-01", "value": 328.86, "realtime_start": "2026-07-14"},
            {"date": "2026-07-01", "value": 329.31, "realtime_start": "2026-08-12"},
            {"date": "2026-08-01", "value": 330.04, "realtime_start": "2026-09-11"},
            {"date": "2026-09-01", "value": 331.082, "realtime_start": "2026-10-13"},
        ],                                                           # "." 的那行已丢
    }


def test_a_raw_series_id_passes_through(fred_key: None) -> None:
    with client(Series()) as c:
        content = download_series(c, "DGS10", as_of=AS_OF)
    assert content["alias"] == "dgs10"
    assert content["fred_id"] == "DGS10"
    assert [row["value"] for row in content["observations"]] == [4.21, 4.19, 4.22]


def test_us10y_is_the_same_series_as_10y_treasury(fred_key: None) -> None:
    with client(Series()) as c:
        content = download_series(c, "us10y", as_of=AS_OF)
    assert content["alias"] == "us10y" and content["fred_id"] == "DGS10"


# ---- 失败与脱敏 ----


def test_an_unknown_series_is_not_covered(fred_key: None) -> None:
    with client(Series()) as c, pytest.raises(NotCovered, match="The series does not exist"):
        download_series(c, "ZZZZZZ", as_of=AS_OF)


def test_error_messages_never_carry_the_key(fred_key: None) -> None:
    with client(Series(status=500)) as c, pytest.raises(SourceUnavailable) as caught:
        download_series(c, "cpi", as_of=AS_OF)
    message = str(caught.value)
    assert KEY not in message
    assert "api_key" not in message                                  # URL 里的密钥参数已删


def test_a_malformed_body_raises_source_unavailable(fred_key: None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>not json</html>")

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as c, \
            pytest.raises(SourceUnavailable, match="不是 JSON"):
        download_series(c, "cpi", as_of=AS_OF)


def test_an_empty_window_is_no_data(fred_key: None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text='{"observations": []}')

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as c, \
            pytest.raises(NoData, match="CPIAUCSL"):
        download_series(c, "cpi", as_of=AS_OF)


def test_a_descriptive_phrase_is_rejected(fred_key: None) -> None:
    with client(Series()) as c, pytest.raises(ValueError, match="不是已知的宏观别名"):
        download_series(c, "bank of japan rate", as_of=AS_OF)
