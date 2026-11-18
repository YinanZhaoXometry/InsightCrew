"""事实账本：把行情 bar 变成 Fact（路径 1），再用 SQL 路径（路径 2）复算核对。

build_ledger 只用路径 1（domain/indicators.py）：对每个声明足够历史的指标，
每个输出产出一条 Fact；历史不足的指标不产出事实，改为每个输出记一条 Unavailable，
并连同数据卡（条数、新旧、缺口、疑似除权）一起返回。一切数值都从调用方给定的
bar 推出，绝不回读更长的序列（时点正确）。
recompute_ledger 只用路径 2（domain/recompute.py）：把同一批 bar 装进内存 SQLite，
按声明逐个调用同名的 SQL 函数。disagreements 比较两条路径，空列表即账本一致。
两条指标路径互不 import、不共享任何计算代码（tests/test_architecture.py 强制）；
这里只做调度与比较。IndicatorSpec 是声明的映像，只在自己人手里传递，不进模型输出。
"""
from __future__ import annotations

import datetime
from contextlib import closing
from itertools import pairwise
from typing import Literal

from pydantic import ConfigDict, model_validator

from . import indicators, recompute
from .market import Bar, Instrument
from .types import Contract, DataCard, Fact, Unavailable


class IndicatorSpec(Contract):
    """指标声明：indicators.toml 里一张表的映像（蓝图 6.1）。不上黑板，不进模型输出格式。"""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key: str                       # 表名，例如 "sma_20"；单输出时也是唯一输出的事实 ID
    fn: str                        # 两条路径里同名的函数，例如 "sma"
    inputs: tuple[str, ...]        # 输入列，取值 open/high/low/close/volume
    args: dict[str, int | float]   # 传给两条路径的关键字参数，例如 {"n": 20}
    outputs: tuple[str, ...]       # 产出的事实 ID，按顺序；多输出时与 labels 一一对应
    labels: tuple[str, ...]        # 每个输出的中文名，用于报告
    min_bars: int                  # 算出值所需的最少 K 线根数，不足则产出 Unavailable
    unit: Literal["price", "pct", "shares", "none"]   # price 换成计价货币；pct 为 "%"；shares 原样；none 为 ""
    formula: str                   # 给交易员、报告和模型看的公式文字，不会被求值

    @model_validator(mode="after")
    def _labels_match_outputs(self) -> IndicatorSpec:
        """labels 与 outputs 必须一一对应，否则报告会对不上号。"""
        if not self.outputs:
            raise ValueError("outputs 不能为空")
        if len(self.labels) != len(self.outputs):
            raise ValueError(f"labels 有 {len(self.labels)} 个，outputs 有 {len(self.outputs)} 个，必须一一对应")
        return self


def build_ledger(instrument: Instrument, bars: tuple[Bar, ...], as_of: datetime.date,
                 specs: tuple[IndicatorSpec, ...], dropped_rows: int) -> tuple[list[Fact], DataCard]:
    """路径 1：每个足够历史的指标按 outputs 产出 Fact，其余产出 Unavailable；附一张数据卡。

    bars 必须非空且全部不晚于 as_of（MarketData.history 的约定），按日期升序。
    Fact 的 as_of 是最后一根 bar 的日期；source 是 indicators.<fn>；bars_used 是传入的根数。
    """
    facts: list[Fact] = []
    unavailable: list[Unavailable] = []
    for spec in specs:
        if len(bars) < spec.min_bars:
            reason = f"历史不足：需要 {spec.min_bars} 根，现有 {len(bars)} 根"
            unavailable.extend(Unavailable(id=output, reason=reason) for output in spec.outputs)
            continue
        value = _path_one(spec, bars)
        if value is None:
            raise ValueError(f"指标 {spec.key}（{spec.fn}）在历史足够（{len(bars)} 根 ≥ {spec.min_bars}）时"
                             "仍返回空值：min_bars 声明与函数定义不一致")
        values = list(value) if isinstance(value, tuple) else [value]
        for output, label, number in zip(spec.outputs, spec.labels, values, strict=True):
            facts.append(Fact(id=output, label=label, value=number,
                              unit=_resolve_unit(spec.unit, instrument),
                              as_of=bars[-1].date.isoformat(),
                              source=f"indicators.{spec.fn}", bars_used=len(bars)))
    return facts, _data_card(bars, as_of, dropped_rows, unavailable)


def _path_one(spec: IndicatorSpec, bars: tuple[Bar, ...]) -> float | tuple[float, ...] | None:
    """按声明的调用约定调用路径 1：fn(*[series[name] for name in inputs], **args)。"""
    fn = getattr(indicators, spec.fn)
    series = [tuple(getattr(bar, column) for bar in bars) for column in spec.inputs]
    return fn(*series, **spec.args)


def _resolve_unit(unit: str, instrument: Instrument) -> str:
    """把声明里的单位换算成 Fact.unit：price 用标的计价货币，其余固定。"""
    if unit == "price":
        return instrument.currency
    if unit == "pct":
        return "%"
    if unit == "shares":
        return "shares"
    return ""                      # "none"：无量纲指标


def _data_card(bars: tuple[Bar, ...], as_of: datetime.date, dropped_rows: int,
               unavailable: list[Unavailable]) -> DataCard:
    """数据卡：条数与新旧、超过五天空档的缺口、单日涨跌超过 40% 的疑似除权/除息。"""
    gaps = [f"{before.date.isoformat()} 到 {after.date.isoformat()} 之间有 {(after.date - before.date).days - 1}"
            " 个自然日没有 K 线"
            for before, after in pairwise(bars) if (after.date - before.date).days - 1 > 5]
    warnings = []
    for before, after in pairwise(bars):
        move = after.close / before.close - 1
        if abs(move) > 0.4:
            warnings.append(f"{after.date.isoformat()} 收盘价较前一日变动 {move * 100:.1f}%，"
                            "可能除权/除息，价格按原值使用")
    return DataCard(first_bar=bars[0].date.isoformat(), last_bar=bars[-1].date.isoformat(),
                    bars=len(bars), stale_days=(as_of - bars[-1].date).days,
                    dropped_rows=dropped_rows, gaps=tuple(gaps),
                    unavailable=tuple(unavailable), warnings=tuple(warnings))


def recompute_ledger(instrument: Instrument, bars: tuple[Bar, ...], as_of: datetime.date,
                     specs: tuple[IndicatorSpec, ...]) -> dict[str, float | None]:
    """路径 2：把同一批 bar 装进内存 SQLite（recompute.load），按声明逐个调用同名 SQL 函数。

    每个输出占返回字典的一项；历史不足时该输出为 None，与路径 1 不产出事实相对应。
    """
    rows = [(instrument.ticker, bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume)
            for bar in bars]
    values: dict[str, float | None] = {}
    with closing(recompute.load(rows)) as db:
        for spec in specs:
            value = getattr(recompute, spec.fn)(db, instrument.ticker, as_of.isoformat(),
                                                spec.inputs, **spec.args)
            if value is None:
                for output in spec.outputs:
                    values[output] = None
            elif isinstance(value, tuple):
                values.update(zip(spec.outputs, value, strict=True))
            else:
                values[spec.outputs[0]] = value
    return values


def disagreements(facts: list[Fact], recomputed: dict[str, float | None]) -> list[str]:
    """两条路径不一致的事实 ID，按事实顺序在前、多出的输出在后；空列表即账本一致。

    两种不一致：数值差超出容差（相对 1e-6 或绝对 1e-9 之内都算一致，与 math.isclose
    的双重容差一致）；或一边有、一边没有。recomputed 里值为 None 表示路径 2 也算不出，
    与路径 1 不产出事实相对应，不算不一致。
    """
    disagree: list[str] = []
    for fact in facts:
        if fact.id not in recomputed:
            disagree.append(fact.id)
            continue
        other = recomputed[fact.id]
        if other is None or _differs(fact.value, other):
            disagree.append(fact.id)
    emitted = {fact.id for fact in facts}
    for output, value in recomputed.items():
        if output not in emitted and value is not None:
            disagree.append(output)
    return disagree


def _differs(a: float, b: float) -> bool:
    """超出容差：差异既超过 1e-6 的相对界限又超过 1e-9 的绝对界限才算不一致。"""
    return abs(a - b) > max(1e-6 * max(abs(a), abs(b)), 1e-9)
