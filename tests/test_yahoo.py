"""WP9A 的行为测试：fetch 用 httpx2.MockTransport 回放 tests/fixtures/yahoo/ 里保存的响应。

不联网。覆盖 BLUEPRINT 12.1 对 test_yahoo.py 的要求：解析保存的响应、日期来自
timestamp + gmtoffset、没有收盘价的行写成空并被加载器丢弃、URL 编码（^HSI → %5EHSI）、
被拒绝或残缺的响应抛 SourceUnavailable 且一个文件都不写；另加基准指数随请求一起
抓取且不重复、universe.toml 能作为 Instrument 读回、失败的下载不留半个文件。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.adapters.yahoo import fetch
from insightcrew.domain.market import SourceUnavailable
from insightcrew.domain.symbols import file_stem

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"


class Replay:
    """按 URL 路径末段（httpx2 已解码成代码本身）回放保存的响应，并记录每个请求供断言。"""

    def __init__(self, refused: dict[str, int] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self._refused = dict(refused or {})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        symbol = request.url.path.rpartition("/")[2]
        if symbol in self._refused:
            return httpx2.Response(self._refused[symbol])
        body = (FIXTURES / f"{file_stem(symbol)}.json").read_bytes()
        return httpx2.Response(200, content=body, headers={"content-type": "application/json"})


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _saved(fixture_file: str, status: int = 200) -> httpx2.Client:
    """把保存的响应文件原样回放的客户端（不管请求的是哪个代码）。"""
    body = (FIXTURES / fixture_file).read_bytes()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=body)

    return _client(handler)


def _refusing(status: int) -> httpx2.Client:
    """只回状态码、没有响应体的客户端：端点拒绝时常常连 body 都没有。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status)

    return _client(handler)


def _symbols(replay: Replay) -> list[str]:
    return [request.url.path.rpartition("/")[2] for request in replay.requests]


def test_fetch_downloads_the_saved_series_and_their_benchmarks(tmp_path: Path) -> None:
    replay = Replay()
    written = fetch(("AAPL", "0700.HK"), 3, tmp_path, _client(replay))
    assert written == ("AAPL", "SPY", "0700.HK", "^HSI")      # 请求的在前，各自的基准随后
    assert _symbols(replay) == ["AAPL", "SPY", "0700.HK", "^HSI"]
    # 恰好这五个文件，没有 .tmp 残留
    assert {path.name for path in (tmp_path / "real").iterdir()} == \
        {"AAPL.csv", "SPY.csv", "0700.HK.csv", "_HSI.csv", "universe.toml"}


def test_the_url_matches_the_blueprint_and_the_symbol_is_url_encoded(tmp_path: Path) -> None:
    replay = Replay()
    fetch(("0700.HK",), 3, tmp_path, _client(replay))
    urls = {request.url.path.rpartition("/")[2]: str(request.url) for request in replay.requests}
    assert urls["0700.HK"] == \
        "https://query1.finance.yahoo.com/v8/finance/chart/0700.HK?range=3y&interval=1d"
    assert urls["^HSI"] == \
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EHSI?range=3y&interval=1d"
    # 浏览器样的 User-Agent，而不是库默认的 python-httpx2/…
    assert all(request.headers["user-agent"].startswith("Mozilla/") for request in replay.requests)


def test_bars_are_parsed_from_the_saved_responses(tmp_path: Path) -> None:
    fetch(("AAPL", "0700.HK"), 3, tmp_path, _client(Replay()))
    market = CsvMarket(tmp_path, "real")
    aapl = market.history("AAPL", datetime.date(2025, 11, 6))
    assert [bar.date for bar in aapl] == [datetime.date(2025, 11, d) for d in (3, 4, 5, 6)]
    assert (aapl[0].open, aapl[0].high, aapl[0].low, aapl[0].close, aapl[0].volume) == \
        (254.2, 257.8, 253.9, 257.1, 41032500.0)
    rows = (tmp_path / "real" / "AAPL.csv").read_text(encoding="utf-8").splitlines()
    assert rows[1] == "2025-11-03,254.2,257.8,253.9,257.1,41032500"   # 数值照数据源原样
    hk = market.history("0700.HK", datetime.date(2025, 11, 7))
    assert hk[0].close == 599.0 and hk[0].volume == 12345600.0
    hsi = market.history("^HSI", datetime.date(2025, 11, 4))
    assert [bar.date for bar in hsi] == [datetime.date(2025, 11, 3), datetime.date(2025, 11, 4)]


def test_dates_come_from_timestamp_plus_gmtoffset(tmp_path: Path) -> None:
    """美股与港股的时区不同：同一批 timestamp 按各自的 gmtoffset 落到正确的本地日期。"""
    fetch(("AAPL", "0700.HK"), 3, tmp_path, _client(Replay()))
    market = CsvMarket(tmp_path, "real")
    assert [bar.date for bar in market.history("AAPL", datetime.date(2025, 11, 6))] == \
        [datetime.date(2025, 11, d) for d in (3, 4, 5, 6)]            # EST（UTC-5）
    assert [bar.date for bar in market.history("0700.HK", datetime.date(2025, 11, 7))] == \
        [datetime.date(2025, 11, d) for d in (3, 4, 5, 6)]            # HKT（UTC+8）


def test_a_row_without_a_close_is_written_with_an_empty_close_and_dropped(tmp_path: Path) -> None:
    fetch(("0700.HK",), 3, tmp_path, _client(Replay()))
    rows = (tmp_path / "real" / "0700.HK.csv").read_text(encoding="utf-8").splitlines()
    assert rows[-1] == "2025-11-07,,,,,"                      # 未结算的交易日照写，close 记空
    market = CsvMarket(tmp_path, "real")
    assert market.dropped_rows("0700.HK") == 1                # 加载器把它丢弃并计数
    assert market.history("0700.HK", datetime.date(2025, 11, 7))[-1].date == \
        datetime.date(2025, 11, 6)                            # 最后一根 bar 是最后一根有收盘价的


def test_universe_toml_validates_as_instruments(tmp_path: Path) -> None:
    fetch(("AAPL", "0700.HK"), 3, tmp_path, _client(Replay()))
    market = CsvMarket(tmp_path, "real")
    aapl = market.instrument("AAPL")
    assert (aapl.name, aapl.market, aapl.currency, aapl.benchmark, aapl.lot, aapl.synthetic) == \
        ("Apple Inc.", "US", "USD", "SPY", 0, False)
    hk = market.instrument("0700.HK")
    assert (hk.name, hk.market, hk.currency, hk.benchmark, hk.synthetic) == \
        ("Tencent Holdings Limited", "HK", "HKD", "^HSI", False)
    spy = market.instrument("SPY")
    assert spy.name == "SPDR S&P 500 ETF Trust"               # 没有 longName 时退到 shortName
    hsi = market.instrument("^HSI")
    assert hsi.market == "HK" and hsi.currency == "HKD"       # 指数跟它所服务的市场走


def test_a_benchmark_is_fetched_once_no_matter_how_many_tickers_need_it(tmp_path: Path) -> None:
    replay = Replay()
    fetch(("0700.HK", "9992.HK"), 3, tmp_path, _client(replay))
    symbols = _symbols(replay)
    assert symbols.count("^HSI") == 1                         # 两个港股共用恒指，只抓一次
    assert set(symbols) == {"0700.HK", "9992.HK", "^HSI"}


@pytest.mark.parametrize("status", [404, 429])
def test_a_refused_response_raises_source_unavailable_with_the_status_and_writes_nothing(
        tmp_path: Path, status: int) -> None:
    with pytest.raises(SourceUnavailable, match=str(status)):
        fetch(("AAPL", "0700.HK"), 3, tmp_path, _refusing(status))
    assert list(tmp_path.iterdir()) == []                     # 一个文件都没写


@pytest.mark.parametrize("fixture_file",
                         ["error.json", "empty.json", "truncated.json", "garbage.html"])
def test_a_malformed_response_raises_source_unavailable_and_writes_nothing(
        tmp_path: Path, fixture_file: str) -> None:
    with pytest.raises(SourceUnavailable):
        fetch(("AAPL",), 3, tmp_path, _saved(fixture_file))
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_leaves_no_partial_file(tmp_path: Path) -> None:
    replay = Replay(refused={"^HSI": 503})                    # 港股下载成功，基准指数被拒绝
    with pytest.raises(SourceUnavailable, match="503"):
        fetch(("0700.HK",), 3, tmp_path, _client(replay))
    assert list(tmp_path.iterdir()) == []                     # 先下载后写盘：不留半个文件，没有 .tmp


def test_a_transport_error_raises_source_unavailable(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("连接被拒绝", request=request)

    with pytest.raises(SourceUnavailable):
        fetch(("AAPL",), 3, tmp_path, _client(handler))
    assert list(tmp_path.iterdir()) == []


def test_without_a_client_fetch_builds_and_closes_one(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    """缺省路径：fetch 自建客户端（带着回放的 transport）、用完自己关掉。全程不联网。"""
    replay = Replay()
    closed: list[bool] = []

    class FakeClient(httpx2.Client):
        def __init__(self, *args, **kwargs) -> None:
            kwargs["transport"] = httpx2.MockTransport(replay)
            super().__init__(*args, **kwargs)

        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(httpx2, "Client", FakeClient)
    assert fetch(("AAPL",), 3, tmp_path) == ("AAPL", "SPY")
    assert closed == [True]
    assert (tmp_path / "real" / "AAPL.csv").exists()
