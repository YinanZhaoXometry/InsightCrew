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

from ..domain.disclosures import Disclosures
from ..domain.ledger import IndicatorSpec
from ..domain.market import MarketData
from ..domain.statements import FundamentalSpec, Statements
from ..domain.types import RuleSpec


class LedgerMarket(MarketData, Protocol):
    """行情端口加上 dropped_rows：账本建数据卡时需要它（CsvMarket 已实现）。"""

    def dropped_rows(self, ticker: str) -> int: ...


@dataclass(frozen=True, slots=True)
class Deps:
    """代码阶段的全部外部依赖。out_dir 与 render 为 None 时（测试中）报告阶段不做任何事。

    证据通道的三个字段带默认值（BLUEPRINT 18.8）：不接线时通道一律不可用，
    于是 FlowConfig.channels 为空的运行与版本 2.0 逐字节相同。
    """

    market: LedgerMarket
    indicators: tuple[IndicatorSpec, ...]
    rules: tuple[RuleSpec, ...]
    today: date                                   # 启动时读一次；IC_TODAY 可以钉住它
    universe: str
    out_dir: Path | None = None                   # 本次运行的目录
    render: Callable[[dict], str] | None = None   # app.report.render，装配时传入
    statements: Statements | None = None          # 年报端口；None = 未接线
    disclosures: Disclosures | None = None        # 公告端口；None = 未接线
    fundamentals: tuple[FundamentalSpec, ...] = ()     # 基本面事实声明
    disclosure_window_days: int = 90              # 公告窗口长度（自然日）
    disclosure_max_items: int = 30                # 交给角色的条目上限
