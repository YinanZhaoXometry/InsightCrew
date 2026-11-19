"""东方财富适配器的行为测试：fetch 用 httpx2.MockTransport 回放 tests/fixtures/eastmoney/ 里的响应。

不联网。AAPL、0700.HK、恒指与 null.json 是 2026-11-19 真实响应的截短；SPY 按同一格式手写。
文件名就是 secid，没有对应文件的 secid 回放 null.json（接口对"这个市场号下没有该代码"的回答）。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.eastmoney import fetch
from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.domain.market import SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "eastmoney"
HKT = datetime.timezone(datetime.timedelta(hours=8))
MORNING = datetime.datetime(2026, 11, 19, 9, 9, tzinfo=HKT)        # 港股今天未收盘，美股 11-18 已收盘
EVENING = datetime.datetime(2026, 11, 19, 17, 0, tzinfo=HKT)       # 港股今天已收盘


class Replay:
    def __init__(self) -> None:
        self.secids: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        secid = request.url.params["secid"]
        self.secids.append(secid)
        path = FIXTURES / f"{secid}.json"
        body = (path if path.exists() else FIXTURES / "null.json").read_bytes()
        return httpx2.Response(200, content=body, headers={"content-type": "application/json"})


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _saved(fixture_file: str) -> httpx2.Client:
    body = (FIXTURES / fixture_file).read_bytes()
    return _client(lambda request: httpx2.Response(200, content=body))


def test_fetch_finds_each_series_by_its_secid(tmp_path: Path) -> None:
    replay = Replay()
    written = fetch(("AAPL", "0700.HK"), 3, tmp_path, now=MORNING, client=_client(replay))
    assert written == ("AAPL", "SPY", "0700.HK", "^HSI")
    assert replay.secids == ["105.AAPL", "107.SPY", "116.00700", "100.HSI"]
    assert {path.name for path in (tmp_path / "real").iterdir()} == \
        {"AAPL.csv", "SPY.csv", "0700.HK.csv", "_HSI.csv", "universe.toml"}


def test_a_us_ticker_tries_the_next_exchange_when_data_is_null(tmp_path: Path) -> None:
    replay = Replay()
    with pytest.raises(SourceUnavailable, match="找不到 MSFT"):
        fetch(("MSFT",), 3, tmp_path, now=MORNING, client=_client(replay))
    assert replay.secids == ["105.MSFT", "106.MSFT", "107.MSFT"]
    assert list(tmp_path.iterdir()) == []


def test_the_request_asks_for_unadjusted_daily_bars_over_the_years_requested(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return Replay()(request)

    fetch(("0700.HK",), 3, tmp_path, now=MORNING, client=_client(handler))
    params = seen[0].url.params
    assert (params["klt"], params["fqt"]) == ("101", "0")
    assert (params["beg"], params["end"]) == ("20231119", "20261119")
    assert seen[0].headers["user-agent"].startswith("Mozilla/")


def test_columns_are_reordered_and_values_kept_as_reported(tmp_path: Path) -> None:
    fetch(("AAPL",), 3, tmp_path, now=MORNING, client=_client(Replay()))
    rows = (tmp_path / "real" / "AAPL.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "date,open,high,low,close,volume"
    assert rows[1] == "2026-11-02,342.670,343.800,335.820,338.320,64757691"
    bar = CsvMarket(tmp_path, "real").history("AAPL", datetime.date(2026, 11, 6))[0]
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (342.67, 343.8, 335.82, 338.32, 64757691.0)


def test_todays_bar_before_the_close_is_written_unsettled_and_dropped(tmp_path: Path) -> None:
    fetch(("0700.HK",), 3, tmp_path, now=MORNING, client=_client(Replay()))
    rows = (tmp_path / "real" / "0700.HK.csv").read_text(encoding="utf-8").splitlines()
    assert rows[-1] == "2026-11-19,,,,,"
    market = CsvMarket(tmp_path, "real")
    assert market.dropped_rows("0700.HK") == 1
    assert market.history("0700.HK", datetime.date(2026, 11, 19))[-1].date == datetime.date(2026, 11, 5)


def test_todays_bar_after_the_close_is_kept(tmp_path: Path) -> None:
    fetch(("0700.HK",), 3, tmp_path, now=EVENING, client=_client(Replay()))
    market = CsvMarket(tmp_path, "real")
    assert market.dropped_rows("0700.HK") == 0
    assert market.history("0700.HK", datetime.date(2026, 11, 19))[-1].date == datetime.date(2026, 11, 19)


def test_universe_toml_validates_as_instruments(tmp_path: Path) -> None:
    fetch(("AAPL", "0700.HK"), 3, tmp_path, now=MORNING, client=_client(Replay()))
    market = CsvMarket(tmp_path, "real")
    aapl = market.instrument("AAPL")
    assert (aapl.name, aapl.market, aapl.currency, aapl.benchmark, aapl.lot, aapl.synthetic) == \
        ("苹果", "US", "USD", "SPY", 0, False)
    hk = market.instrument("0700.HK")
    assert (hk.name, hk.currency, hk.benchmark) == ("腾讯控股", "HKD", "^HSI")
    assert market.instrument("^HSI").currency == "HKD"


@pytest.mark.parametrize("status", [403, 502])
def test_a_refused_response_raises_and_writes_nothing(tmp_path: Path, status: int) -> None:
    with pytest.raises(SourceUnavailable, match=str(status)):
        fetch(("AAPL",), 3, tmp_path, now=MORNING, client=_client(lambda request: httpx2.Response(status)))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fixture_file", ["empty.json", "truncated.json", "garbage.html"])
def test_a_malformed_response_raises_and_writes_nothing(tmp_path: Path, fixture_file: str) -> None:
    with pytest.raises(SourceUnavailable):
        fetch(("AAPL",), 3, tmp_path, now=MORNING, client=_saved(fixture_file))
    assert list(tmp_path.iterdir()) == []


def test_a_failed_benchmark_leaves_no_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["secid"] == "100.HSI":
            return httpx2.Response(503)
        return Replay()(request)

    with pytest.raises(SourceUnavailable, match="503"):
        fetch(("0700.HK",), 3, tmp_path, now=MORNING, client=_client(handler))
    assert list(tmp_path.iterdir()) == []


def test_a_transport_error_raises_source_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("连接被拒绝", request=request)

    with pytest.raises(SourceUnavailable):
        fetch(("AAPL",), 3, tmp_path, now=MORNING, client=_client(handler))
    assert list(tmp_path.iterdir()) == []
