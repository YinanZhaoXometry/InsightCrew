"""真实宇宙的盘上格式：要抓哪些序列，以及怎样把下载结果写成 CsvMarket 读的目录。

两个下载适配器（yahoo、eastmoney）共用：它们只负责各自数据源的端点与响应格式，
把每个序列解析成（CSV 文本，Instrument）后交给 write。写盘规则只在这里：
每个文件先写临时文件再改名，universe.toml 最后写——宇宙一出现，它引用的每个 CSV 都已在盘上。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from ..domain.market import Instrument
from ..domain.symbols import benchmark_for, file_stem

Market = Literal["US", "HK"]                     # Instrument.market 的两个字面量
COLUMNS = "date,open,high,low,close,volume"


def plan(tickers: Sequence[str]) -> tuple[list[str], dict[str, Market]]:
    """要下载的代码与各自的市场：请求的在前，基准随后，每个代码只出现一次。

    基准与它所服务的代码同市场（benchmark_for 的语义本来如此），所以 ^HSI
    随港股请求进入 HK 市场。基准重复出现只抓一次。
    """
    todo: list[str] = []
    markets: dict[str, Market] = {}
    for ticker in tickers:
        market: Market = "HK" if ticker.endswith(".HK") else "US"
        for code in (ticker, benchmark_for(ticker)):
            if code not in todo:
                todo.append(code)
                markets[code] = market
    return todo, markets


def write(root: str | Path, series: Sequence[tuple[str, Instrument]]) -> tuple[str, ...]:
    """把已全部下载并解析好的序列写进 root/real/，返回写出的代码。"""
    target = Path(root) / "real"
    target.mkdir(parents=True, exist_ok=True)
    for text, instrument in series:
        _atomic_write(target / f"{file_stem(instrument.ticker)}.csv", text)
    _atomic_write(target / "universe.toml", _universe_toml([instrument for _, instrument in series]))
    return tuple(instrument.ticker for _, instrument in series)


def _universe_toml(instruments: list[Instrument]) -> str:
    """把 Instrument 排成 universe.toml。表名与字符串值用 TOML 基本字符串（json.dumps 同一套转义）。"""
    lines: list[str] = []
    for instrument in instruments:
        lines.append(f"[{json.dumps(instrument.ticker)}]")
        fields = instrument.model_dump()
        fields.pop("ticker")                              # 表名就是代码，表内不再重复
        for key, value in fields.items():
            lines.append(f"{key} = {json.dumps(value)}")
        lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    """写临时文件再改名：磁盘上要么是旧文件要么是完整的新文件，永远看不到半个。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)
