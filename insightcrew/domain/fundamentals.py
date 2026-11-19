"""基本面的第一条计算路径（Python）。基本面账本（fundamentals_ledger）用它。

每个函数接收同一批年报行（已由端口截止到分析日）、声明的 concepts、分析日收盘价
与分析日本身，返回一个数；缺输入时返回 None，绝不拿 0 顶替，也不跨币种换算。
选择规则固定在 BLUEPRINT 18.6，两条路径必须一致：
    最新财年 = concept 行里 period_end 最大者；
    一个财年的取值 = 该财年行里 filed 最晚的那次申报（重述后为准）；
    上一财年 = 比最新财年早 330 到 400 天的 period_end 中最大者。
本模块只用标准库（18.6）：年报行以结构匹配（_Row 协议）接收，不 import statements。
"""
from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Protocol


class _Row(Protocol):
    """一条年报行的形状；statements.Reported 结构匹配。只为类型检查存在。

    成员用只读 property：frozen dataclass 的字段不可赋值，可变协议成员匹配不上。
    """

    @property
    def concept(self) -> str: ...

    @property
    def period_end(self) -> datetime.date: ...

    @property
    def period_days(self) -> int: ...

    @property
    def value(self) -> float: ...

    @property
    def filed(self) -> datetime.date: ...

    @property
    def form(self) -> str: ...


def _rows(rows: Sequence[_Row], concept: str) -> list[_Row]:
    return [r for r in rows if r.concept == concept]


def latest_year(rows: Sequence[_Row], concept: str) -> datetime.date | None:
    years = [r.period_end for r in _rows(rows, concept)]
    return max(years) if years else None


def year_rows(rows: Sequence[_Row], concept: str,
              year: datetime.date) -> list[_Row]:
    """一个 concept 在一个财年的全部申报，按申报日升序（重述在后）。"""
    return sorted((r for r in _rows(rows, concept) if r.period_end == year),
                  key=lambda r: r.filed)


def year_value(rows: Sequence[_Row], concept: str, year: datetime.date) -> float | None:
    filed = year_rows(rows, concept, year)
    return filed[-1].value if filed else None


def previous_year(rows: Sequence[_Row], concept: str,
                  latest: datetime.date) -> datetime.date | None:
    gaps = [r.period_end for r in _rows(rows, concept)
            if 330 <= (latest - r.period_end).days <= 400]
    return max(gaps) if gaps else None


def rows_used(rows: Sequence[_Row], concepts: Sequence[str], fn: str) -> tuple[_Row, ...]:
    """一个声明实际用到的行。账本用它填 Fact.as_of（所用输入里最晚的申报日）。"""
    year = latest_year(rows, concepts[0])
    if year is None:
        return ()
    used = list(year_rows(rows, concepts[0], year))
    if fn == "yoy_pct":
        prev = previous_year(rows, concepts[0], year)
        if prev is not None:
            used += year_rows(rows, concepts[0], prev)
    elif fn == "ratio_pct":
        used += year_rows(rows, concepts[1], year)
    return tuple(used)


def latest(rows: Sequence[_Row], concepts: Sequence[str], *,
           close: float, as_of: datetime.date) -> float | None:
    year = latest_year(rows, concepts[0])
    return None if year is None else year_value(rows, concepts[0], year)


def yoy_pct(rows: Sequence[_Row], concepts: Sequence[str], *,
            close: float, as_of: datetime.date) -> float | None:
    year = latest_year(rows, concepts[0])
    if year is None:
        return None
    current = year_value(rows, concepts[0], year)
    prev_year = previous_year(rows, concepts[0], year)
    if current is None or prev_year is None:
        return None
    previous = year_value(rows, concepts[0], prev_year)
    if previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def ratio_pct(rows: Sequence[_Row], concepts: Sequence[str], *,
              close: float, as_of: datetime.date) -> float | None:
    """两个 concept 在 concepts[0] 的最新财年上的比值；分母缺失或为零时算不出。"""
    year = latest_year(rows, concepts[0])
    if year is None:
        return None
    top = year_value(rows, concepts[0], year)
    bottom = year_value(rows, concepts[1], year)
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom * 100


def price_multiple(rows: Sequence[_Row], concepts: Sequence[str], *,
                   close: float, as_of: datetime.date) -> float | None:
    value = latest(rows, concepts, close=close, as_of=as_of)
    if value is None or value <= 0:
        return None
    return close / value


def filing_age(rows: Sequence[_Row], concepts: Sequence[str], *,
               close: float, as_of: datetime.date) -> float | None:
    """最近财年最晚一次申报到分析日的天数：衡量这套数字有多旧。"""
    year = latest_year(rows, concepts[0])
    if year is None:
        return None
    filed = year_rows(rows, concepts[0], year)
    return (as_of - filed[-1].filed).days
