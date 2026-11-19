"""基本面事实账本：把年报行变成 Fact（路径 1），再用 SQL 路径（路径 2）复算核对。

与 domain/ledger.py 同样的分工：build_fundamentals 只用 domain/fundamentals.py，
recompute_fundamentals 只用 domain/fundamentals_sql.py；两条路径互不 import、
不共享计算代码（tests/test_architecture.py 强制）；这里只做调度与 Fact 的元数据
（label、单位、as_of、重述提醒），数值本身全部出自路径 1。金额以百万计
（BLUEPRINT 决策 38）：两个 builder 都做同一换算，disagreements 才能直接比较。
"""
from __future__ import annotations

import datetime
from contextlib import closing

from . import fundamentals, fundamentals_sql
from .market import Instrument
from .statements import FundamentalSpec, StatementSet
from .types import Fact, FundamentalsCard, Unavailable

_MILLION = 1_000_000


def build_fundamentals(instrument: Instrument, statements: StatementSet, close: float,
                       close_date: datetime.date, as_of: datetime.date,
                       specs: tuple[FundamentalSpec, ...]) -> tuple[list[Fact], FundamentalsCard]:
    """路径 1：每张声明产出一条 Fact；算不出的产出一条 Unavailable，附一张数据卡。

    close 是分析日（或其前最后一根 bar）的收盘价，只有 price_multiple 用到它。
    Fact.as_of 是所用输入里最晚的申报日；price_multiple 另看 bar 日期（18.4）。
    """
    facts: list[Fact] = []
    unavailable: list[Unavailable] = []
    currency_differs = statements.currency != instrument.currency
    for spec in specs:
        if currency_differs and _needs_price(spec.fn):
            unavailable.append(Unavailable(
                id=spec.key,
                reason=f"申报货币 {statements.currency} 与标的计价货币 {instrument.currency} 不同，不做换算"))
            continue
        value = getattr(fundamentals, spec.fn)(statements.rows, spec.concepts,
                                               close=close, as_of=as_of)
        if value is None:
            unavailable.append(Unavailable(id=spec.key, reason=_why_none(spec)))
            continue
        used = fundamentals.rows_used(statements.rows, spec.concepts, spec.fn)
        newest_filed = max((r.filed for r in used), default=as_of)
        fact_date = max(newest_filed, close_date) if _needs_price(spec.fn) else newest_filed
        year = fundamentals.latest_year(statements.rows, spec.concepts[0])
        facts.append(Fact(
            id=spec.key,
            label=f"{spec.label}（最近财年，截至 {year.isoformat() if year else '无'}）",
            value=value / _MILLION if spec.unit == "money" else value,
            unit=_unit(spec.unit, statements.currency),
            as_of=fact_date.isoformat(),
            source=f"fundamentals.{spec.fn}", bars_used=0))
    return facts, _card(statements, specs, unavailable)


def recompute_fundamentals(statements: StatementSet, close: float, as_of: datetime.date,
                           specs: tuple[FundamentalSpec, ...]) -> dict[str, float | None]:
    """路径 2：同一批行装进内存 SQLite，按声明逐个调用同名 SQL 函数。

    每张声明占返回字典的一项；算不出为 None，与路径 1 不产出事实相对应。
    金额同样以百万计。调用方决定传入哪些声明：申报货币与标的货币不同时，
    依赖价格的声明（pe_fy）不应传入（见 HANDOFF 对 18.6 缺口的记录）。
    """
    values: dict[str, float | None] = {}
    with closing(fundamentals_sql.load(statements.rows)) as db:
        for spec in specs:
            value = getattr(fundamentals_sql, spec.fn)(db, spec.concepts,
                                                       close=close, as_of=as_of)
            values[spec.key] = value / _MILLION if (value is not None
                                                    and spec.unit == "money") else value
    return values


def _needs_price(fn: str) -> bool:
    return fn == "price_multiple"


def _why_none(spec: FundamentalSpec) -> str:
    return {"latest": "年报里没有这个概念的申报",
            "yoy_pct": "缺上一财年（相隔 330 到 400 天）的申报，或其值为零，算不出同比",
            "ratio_pct": "分子或分母缺同财年的申报，或分母为零，算不出比值",
            "price_multiple": "每股收益缺失或不为正，算不出市盈率",
            "filing_age": "年报里没有这个概念的申报，算不出申报间隔"}[spec.fn]


def _unit(unit: str, currency: str) -> str:
    if unit == "money":
        return f"{currency} million"
    if unit == "per_share":
        return currency
    if unit == "pct":
        return "%"
    if unit == "days":
        return "days"
    return ""                       # ratio：无量纲


def _card(statements: StatementSet, specs: tuple[FundamentalSpec, ...],
          unavailable: list[Unavailable]) -> FundamentalsCard:
    """数据卡：来源、货币、整体画像与重述提醒（一个财年被申报多于一次）。"""
    warnings: list[str] = []
    for concept in sorted({c for spec in specs for c in spec.concepts}):
        year = fundamentals.latest_year(statements.rows, concept)
        if year is None:
            continue
        dates = sorted(r.filed for r in statements.rows
                       if r.concept == concept and r.period_end == year)
        if len(dates) > 1:
            warnings.append(f"{concept} 的 {year.isoformat()} 财年被申报过 {len(dates)} 次"
                            f"（{'、'.join(d.isoformat() for d in dates)}），"
                            "所用数字是最后一次申报的口径，此前的申报已被重述")
    return FundamentalsCard(
        source=statements.source,
        currency=statements.currency,
        fiscal_year_end=max(r.period_end for r in statements.rows).isoformat()
        if statements.rows else "",          # 空数据集：evidence 会先判 empty，这里只是防御
        filed=max(r.filed for r in statements.rows).isoformat() if statements.rows else "",
        rows=len(statements.rows),
        unavailable=tuple(unavailable), warnings=tuple(warnings))
