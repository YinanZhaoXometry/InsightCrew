"""代码阶段需要的外部依赖。由 app 层装配（app/wiring.py），dataclass，不跨进程。

flows 不 import app，也不 import adapters：市场数据在这里只是 domain 的 MarketData 端口
（加上数据卡需要的一件事：文件里被丢弃的行数，见 BLUEPRINT 9.3），具体实现由装配处决定。
app 把 render 作为函数传入，因为 flows 不能依赖 app。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from ..domain.ledger import IndicatorSpec
from ..domain.market import MarketData
from ..domain.types import RuleSpec


class LedgerMarket(MarketData, Protocol):
    """行情端口加上 dropped_rows：账本建数据卡时需要它（CsvMarket 已实现）。"""

    def dropped_rows(self, ticker: str) -> int: ...


@dataclass(frozen=True, slots=True)
class Deps:
    """代码阶段的全部外部依赖。out_dir 与 render 为 None 时（测试中）报告阶段不做任何事。"""

    market: LedgerMarket
    indicators: tuple[IndicatorSpec, ...]
    rules: tuple[RuleSpec, ...]
    today: date                                   # 启动时读一次；IC_TODAY 可以钉住它
    universe: str
    out_dir: Path | None = None                   # 本次运行的目录
    render: Callable[[dict], str] | None = None   # app.report.render，装配时传入
