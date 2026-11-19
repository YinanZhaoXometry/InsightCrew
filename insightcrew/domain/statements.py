"""年报数据的契约与端口：公司按申报口径给出的年度数字（BLUEPRINT 18.5）。

和 domain/market.py 一样：这里只有端口、端口返回的普通值和声明契约，没有任何 I/O。
读取由 adapters 层实现（statements_json、edgar）。时点规则（18.5 的八条）由端口保证：
不返回申报日晚于分析日的行，也没有任何方法能拿到更晚的行。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal, Protocol

from .types import Contract


@dataclass(frozen=True, slots=True)
class Reported:
    """一份年报里的一个数字。金额为申报货币的原单位（不做百万换算，换算在账本做）。"""

    concept: str                  # 概念：revenue、net_income、eps_diluted 等，见 18.6 的注册表
    period_end: datetime.date     # 财年期末
    period_days: int              # 流量类一年为 350 到 380；存量类（资产负债）为 0
    value: float                  # 申报原值，申报货币
    filed: datetime.date          # 申报公开之日
    form: str                     # 报表类型，例如 "10-K"、"年报"


@dataclass(frozen=True, slots=True)
class StatementSet:
    """一次读取得到的全部分年报行。"""

    source: str
    currency: str
    rows: tuple[Reported, ...]


class Statements(Protocol):
    """年报端口。给一个代码和 分析日，返回 分析日（含）之前已公开的年度数字。

    抛 NotCovered（来源没有这个标的，或覆盖不到这个日期）、SourceUnavailable（读不出来）。
    """

    def statements(self, ticker: str, as_of: datetime.date) -> StatementSet: ...


class FundamentalSpec(Contract):
    """基本面事实的声明（data/fundamentals.toml 的一行），与 IndicatorSpec 同构。"""

    key: str                      # 事实 ID，也是表键
    fn: str                       # 计算函数名，见 domain/fundamentals.py
    concepts: tuple[str, ...]     # 用到的 concept
    unit: Literal["money", "per_share", "pct", "ratio", "days"]
    label: str                    # 中文名，用于报告；账本会补上财年期末
    formula: str                  # 公式文字，不会被求值
