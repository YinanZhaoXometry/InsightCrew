"""MarketData 端口的文件实现：读 data/market/<universe>/ 下的 universe.toml 与日线 CSV。

universe.toml 在构造时读一次，每张表校验成一个 Instrument（表名就是代码，
未知字段在这里被拒绝）；每个 CSV 只读一次并缓存。close 为空的行不是一根 bar：
序列末尾是未结算的交易日，序列中间是坏行，两者都丢弃并计数，绝不填补。
CSV 读不出来或格式不对抛 SourceUnavailable，永远不返回空序列。
"""
from __future__ import annotations

import csv
import datetime
import tomllib
from pathlib import Path

from ..domain.market import Bar, Instrument, NoData, SourceUnavailable, UnknownTicker
from ..domain.symbols import file_stem

_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class CsvMarket:
    """一个宇宙 = root/universe/ 目录下的 universe.toml 加每个代码一个 <file_stem>.csv。"""

    def __init__(self, root: str | Path, universe: str):
        self._dir = Path(root) / universe
        with (self._dir / "universe.toml").open("rb") as f:
            tables = tomllib.load(f)
        self._instruments: dict[str, Instrument] = {
            ticker: Instrument.model_validate({"ticker": ticker, **table})
            for ticker, table in tables.items()
        }
        self._bars: dict[str, list[Bar]] = {}
        self._dropped: dict[str, int] = {}

    # ---- MarketData 端口 ----

    def instrument(self, ticker: str) -> Instrument:
        try:
            return self._instruments[ticker]
        except KeyError:
            raise UnknownTicker(f"{ticker} 不在宇宙 {self._dir.name} 的 universe.toml 里") from None

    def history(self, ticker: str, as_of: datetime.date) -> tuple[Bar, ...]:
        """as_of（含）之前已结算的 bar，升序。没有一根就抛 NoData。"""
        bars = [bar for bar in self._load(ticker) if bar.date <= as_of]
        if not bars:
            raise NoData(f"{ticker} 在 {as_of}（含）之前没有已结算的 bar")
        return tuple(bars)

    # ---- 数据卡用的额外信息 ----

    def dropped_rows(self, ticker: str) -> int:
        """整个文件里 close 为空、被丢弃的行数（含序列末尾的未结算交易日）。"""
        self._load(ticker)
        return self._dropped[ticker]

    # ---- 内部 ----

    def _load(self, ticker: str) -> list[Bar]:
        """读一个 CSV（每个文件只读一次），丢弃 close 为空的行，按日期升序缓存。"""
        if ticker in self._bars:
            return self._bars[ticker]
        if ticker not in self._instruments:
            raise UnknownTicker(f"{ticker} 不在宇宙 {self._dir.name} 的 universe.toml 里")
        path = self._dir / f"{file_stem(ticker)}.csv"
        bars: list[Bar] = []
        dropped = 0
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header is None or tuple(cell.strip() for cell in header) != _COLUMNS:
                    raise SourceUnavailable(f"{path} 的表头不是 {','.join(_COLUMNS)}")
                for lineno, row in enumerate(reader, start=2):    # 第 1 行是表头
                    if not row:
                        continue
                    if len(row) != len(_COLUMNS):
                        raise SourceUnavailable(f"{path} 第 {lineno} 行的列数不对：{row!r}")
                    day_text, _, _, _, close_text, _ = (cell.strip() for cell in row)
                    try:
                        day = datetime.date.fromisoformat(day_text)
                    except ValueError:
                        raise SourceUnavailable(
                            f"{path} 第 {lineno} 行的日期无法解析：{day_text!r}") from None
                    if not close_text:
                        dropped += 1          # 没有收盘价就不是一根 bar，丢弃并计数
                        continue
                    try:
                        bar = Bar(date=day,
                                  open=float(row[1]), high=float(row[2]), low=float(row[3]),
                                  close=float(close_text), volume=float(row[5]))
                    except ValueError:
                        raise SourceUnavailable(f"{path} 第 {lineno} 行的数值无法解析") from None
                    bars.append(bar)
        except (OSError, ValueError, csv.Error) as e:
            raise SourceUnavailable(f"读不出 {path}：{e}") from e
        bars.sort(key=lambda bar: bar.date)
        self._bars[ticker] = bars
        self._dropped[ticker] = dropped
        return bars
