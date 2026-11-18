"""WP2 的行为测试：代码规范化（BLUEPRINT 6.2）、CSV 读取（9.3）、合成宇宙的形状与生成器的确定性。

凡是容易构造的边界（坏行、坏表、缺文件）都用 tmp_path 里的手写小宇宙；
合成宇宙本身的数据形状（THIN/STALE/GAPPY）与逐日扫描用 data/market/synthetic/ 的真数据。
"""
from __future__ import annotations

import csv
import itertools
import subprocess
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.domain.market import Bar, InvalidTicker, NoData, SourceUnavailable, UnknownTicker
from insightcrew.domain.symbols import benchmark_for, file_stem, normalize

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "data" / "market" / "synthetic"
GENERATOR = ROOT / "data" / "make_market.py"
AS_OF = date(2026, 9, 30)          # 合成宇宙的最后一天
TICKERS = ("ACME", "0000.HK", "SYNUS", "SYNHK", "THIN", "STALE", "GAPPY")

_TABLE = """\
[ACME]
name = "Test Acme（合成数据）"
market = "US"
currency = "USD"
benchmark = "SYNUS"
lot = 1
synthetic = true
"""

_HK_TABLE = """\
["0000.HK"]
name = "Test Zero（合成数据）"
market = "HK"
currency = "HKD"
benchmark = "SYNHK"
lot = 100
synthetic = true
"""

_HEADER = "date,open,high,low,close,volume\n"


def _csv(*rows: str) -> str:
    return _HEADER + "".join(row + "\n" for row in rows)


def _local(tmp: Path, tables: str = _TABLE,
           files: dict[str, str] | None = None) -> CsvMarket:
    """在 tmp/u/ 下搭一个手写宇宙。files 为 None 时写一行常规数据，为 {} 时不写任何 CSV。"""
    universe = tmp / "u"
    universe.mkdir()
    (universe / "universe.toml").write_text(tables, encoding="utf-8")
    if files is None:
        files = {"ACME": _csv("2025-01-02,10.00,10.50,9.80,10.20,1000")}
    for name, text in files.items():
        (universe / f"{name}.csv").write_text(text, encoding="utf-8")
    return CsvMarket(tmp, "u")


def _business_days() -> list[date]:
    """生成器所用的业务日：2025-01-02 到 2026-09-30 的周一到周五。"""
    days: list[date] = []
    day = date(2025, 1, 2)
    while day <= AS_OF:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


@pytest.fixture(scope="module")
def market() -> CsvMarket:
    return CsvMarket(ROOT / "data" / "market", "synthetic")


# ---- 6.2 代码规范化 ----

@pytest.mark.parametrize(("text", "expected"), [
    pytest.param("700.HK", "0700.HK", id="hk-补齐四位"),
    pytest.param("09992.HK", "9992.HK", id="hk-五位去前导零"),
    pytest.param("5.HK", "0005.HK", id="hk-一位补齐"),
    pytest.param("949.hk", "0949.HK", id="hk-去空白转大写"),
    pytest.param("99992.HK", "99992.HK", id="hk-五位无前导零原样保留"),
    pytest.param("AAPL", "AAPL", id="us"),
    pytest.param("AAPL12", "AAPL12", id="us-六位"),
])
def test_normalize_accepts(text: str, expected: str) -> None:
    assert normalize(text) == expected


@pytest.mark.parametrize("text", [
    "../x",              # 路径穿越字符串，绝不能进文件名
    "BRK.B",             # 美股不接受点
    "7203.T",            # 只做美股和港股
    "^HSI",              # 指数代码只作基准，不接受用户输入
    "",                  # 空文本
    "AAPL123",           # 美股最长六位
    "0700 HK",           # 中间有空格，不是代码
])
def test_normalize_rejects(text: str) -> None:
    with pytest.raises(InvalidTicker):
        normalize(text)


@pytest.mark.parametrize(("ticker", "stem"), [
    ("^HSI", "_HSI"),
    ("0700.HK", "0700.HK"),
    ("AAPL", "AAPL"),
])
def test_file_stem(ticker: str, stem: str) -> None:
    assert file_stem(ticker) == stem


@pytest.mark.parametrize(("ticker", "bench"), [
    ("0700.HK", "^HSI"),
    ("9992.HK", "^HSI"),
    ("AAPL", "SPY"),
    ("ACME", "SPY"),
])
def test_benchmark_for(ticker: str, bench: str) -> None:
    assert benchmark_for(ticker) == bench


# ---- 9.3 CSV 读取 ----

def test_bar_values_come_from_the_file(tmp_path: Path) -> None:
    bars = _local(tmp_path).history("ACME", date(2025, 1, 2))
    assert bars == (Bar(date=date(2025, 1, 2), open=10.0, high=10.5, low=9.8,
                        close=10.2, volume=1000.0),)


def test_history_is_inclusive_and_ascending(tmp_path: Path) -> None:
    text = _csv("2025-01-02,10.00,10.50,9.80,10.20,1000",
                "2025-01-03,10.20,10.60,10.00,10.30,1000",
                "2025-01-06,10.30,10.70,10.10,10.40,1000")
    market = _local(tmp_path, files={"ACME": text})
    assert [b.date for b in market.history("ACME", date(2025, 1, 2))] == [date(2025, 1, 2)]
    assert [b.date for b in market.history("ACME", date(2025, 1, 7))] == \
        [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def test_empty_close_rows_are_dropped_and_counted(tmp_path: Path) -> None:
    text = _csv("2025-01-02,10.00,10.50,9.80,10.20,1000",
                "2025-01-03,10.20,10.60,10.00,,1000",        # 中间的坏行
                "2025-01-06,10.30,10.70,10.10,10.40,1000",
                "2025-01-07,10.40,10.80,10.20,,1000")        # 序列末尾的未结算交易日
    market = _local(tmp_path, files={"ACME": text})
    assert market.dropped_rows("ACME") == 2
    # 两行都丢弃，中间的缺口不填补
    assert [b.date for b in market.history("ACME", date(2025, 1, 7))] == \
        [date(2025, 1, 2), date(2025, 1, 6)]


@pytest.mark.parametrize("text", [
    _csv("2025-01-02,10.00,10.50,9.80,10.20"),                    # 列数不对
    _csv("not-a-date,10.00,10.50,9.80,10.20,1000"),               # 日期坏
    _csv("2025-01-02,ten,10.50,9.80,10.20,1000"),                 # 数值坏
    _csv("2025-01-02,10.00,10.50,9.80,10.20,"),                   # 空 volume（只有 close 允许为空）
    "open,high,low\n10.0,10.5,9.8\n",                             # 表头列不对
])
def test_malformed_csv_raises_source_unavailable(tmp_path: Path, text: str) -> None:
    market = _local(tmp_path, files={"ACME": text})
    with pytest.raises(SourceUnavailable):
        market.history("ACME", date(2025, 1, 2))


def test_header_only_csv_has_no_bars(tmp_path: Path) -> None:
    """文件读得出来但没有一行数据：不是坏源，是 NoData，也不算丢弃行。"""
    market = _local(tmp_path, files={"ACME": _HEADER})
    with pytest.raises(NoData):
        market.history("ACME", date(2025, 1, 2))
    assert market.dropped_rows("ACME") == 0


def test_missing_csv_raises_source_unavailable(tmp_path: Path) -> None:
    market = _local(tmp_path, files={})
    assert market.instrument("ACME").ticker == "ACME"     # 静态描述来自 toml，不受影响
    with pytest.raises(SourceUnavailable):
        market.history("ACME", date(2025, 1, 2))


def test_no_data_before_first_bar(tmp_path: Path) -> None:
    market = _local(tmp_path)
    with pytest.raises(NoData):
        market.history("ACME", date(2024, 12, 31))


def test_unknown_ticker(tmp_path: Path) -> None:
    market = _local(tmp_path, tables=_TABLE + _HK_TABLE,
                    files={"ACME": _csv("2025-01-02,10.00,10.50,9.80,10.20,1000"),
                           "0000.HK": _csv("2025-01-02,30.00,30.50,29.80,30.20,2000")})
    with pytest.raises(UnknownTicker):
        market.instrument("NOPE")
    with pytest.raises(UnknownTicker):
        market.history("NOPE", date(2025, 1, 2))
    # 适配器按原样查表：未规范化的用户写法（700.HK）不在宇宙里，尽管 0000.HK 在
    with pytest.raises(UnknownTicker):
        market.history("700.HK", date(2025, 1, 2))
    assert market.history("0000.HK", date(2025, 1, 2))[0].close == 30.20


def test_universe_toml_unknown_field_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as e:
        _local(tmp_path, tables=_TABLE + 'sector = "tech"\n')
    assert "sector" in str(e.value)                       # 错误信息指名这个字段


# ---- 合成宇宙 ----

def test_history_never_returns_bar_after_as_of(market: CsvMarket) -> None:
    """逐日扫描：2024-12-01 到 2026-10-31 的每一天，返回的都恰是不晚于该日的前缀。"""
    first, last = date(2024, 12, 1), date(2026, 10, 31)
    for ticker in TICKERS:
        full = market.history(ticker, date(2027, 1, 1))
        full_dates = [bar.date for bar in full]
        assert full_dates == sorted(full_dates), ticker
        for as_of in (first + timedelta(days=n) for n in range((last - first).days + 1)):
            if as_of < full_dates[0]:
                with pytest.raises(NoData):
                    market.history(ticker, as_of)
                continue
            bars = market.history(ticker, as_of)
            assert bars[-1].date <= as_of, (ticker, as_of)
            assert [bar.date for bar in bars] == [d for d in full_dates if d <= as_of], \
                (ticker, as_of)


def test_as_of_on_the_last_day_includes_that_bar(market: CsvMarket) -> None:
    assert market.history("ACME", AS_OF)[-1].date == AS_OF


def test_every_table_is_a_validated_instrument(market: CsvMarket) -> None:
    with (SYNTHETIC / "universe.toml").open("rb") as f:
        assert set(tomllib.load(f)) == set(TICKERS)       # 宇宙恰好是这七个标的
    for ticker in TICKERS:
        inst = market.instrument(ticker)
        assert inst.ticker == ticker
        assert inst.name.endswith("（合成数据）")
        assert inst.synthetic is True
        assert inst.market in ("US", "HK")


def test_acme_and_hk_instrument_fields(market: CsvMarket) -> None:
    acme = market.instrument("ACME")
    assert (acme.market, acme.currency, acme.benchmark, acme.lot) == ("US", "USD", "SYNUS", 1)
    hk = market.instrument("0000.HK")
    assert (hk.market, hk.currency, hk.benchmark, hk.lot) == ("HK", "HKD", "SYNHK", 100)


def test_thin_has_only_the_last_40_business_days(market: CsvMarket) -> None:
    days = _business_days()
    bars = market.history("THIN", AS_OF)
    assert [bar.date for bar in bars] == days[-40:]
    with pytest.raises(NoData):
        market.history("THIN", days[-41])


def test_stale_ends_on_2026_08_31(market: CsvMarket) -> None:
    bars = market.history("STALE", AS_OF)
    assert bars[-1].date == date(2026, 8, 31)
    assert len(bars) == len([d for d in _business_days() if d <= date(2026, 8, 31)])


def test_gappy_reports_two_dropped_rows_and_last_bar_is_last_row_with_close(
        market: CsvMarket) -> None:
    with (SYNTHETIC / "GAPPY.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    empty_dates = {date.fromisoformat(row["date"]) for row in rows if not row["close"]}
    assert len(empty_dates) == 2
    assert max(empty_dates) == AS_OF                          # 最后一行是空收盘
    assert market.dropped_rows("GAPPY") == 2
    bars = market.history("GAPPY", AS_OF)
    bar_dates = {bar.date for bar in bars}
    assert not empty_dates & bar_dates                        # 两行都没有成为 bar
    last_with_close = max(date.fromisoformat(row["date"]) for row in rows if row["close"])
    assert bars[-1].date == last_with_close < AS_OF           # 最后一根 bar 是最后一根有收盘价的行


def test_gappy_has_one_week_hole_in_mid_series(market: CsvMarket) -> None:
    with (SYNTHETIC / "GAPPY.csv").open(encoding="utf-8", newline="") as f:
        dates = [date.fromisoformat(row["date"]) for row in csv.DictReader(f)]
    holes = []
    for earlier, later in itertools.pairwise(dates):
        day, missing = earlier + timedelta(days=1), 0
        while day < later:
            missing += day.weekday() < 5
            day += timedelta(days=1)
        if missing:
            holes.append(missing)
    assert holes == [5]                                       # 恰好一个洞，恰好一个交易周


def test_acme_mild_uptrend(market: CsvMarket) -> None:
    bars = market.history("ACME", AS_OF)
    assert bars[0].close < bars[-1].close


# ---- 生成器 ----

def _snapshot() -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(SYNTHETIC.iterdir())}


def test_generator_writes_the_same_bytes_twice() -> None:
    subprocess.run([sys.executable, GENERATOR], check=True, capture_output=True)
    first = _snapshot()
    subprocess.run([sys.executable, GENERATOR], check=True, capture_output=True)
    assert _snapshot() == first
    expected = {SYNTHETIC / f"{ticker.replace('^', '_')}.csv" for ticker in TICKERS}
    expected |= {SYNTHETIC / "universe.toml"}
    assert set(first) == expected
