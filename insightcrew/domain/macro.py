"""宏观数据的契约、端口与第一条计算路径（Python，BLUEPRINT 19.7、19.8、19.12）。

和 domain/statements.py 一样：这里只有端口、端口返回的普通值、声明契约和纯计算，
没有任何 I/O；读取由 adapters 层实现（macro_json 读档案，fred 走实时，WP30）。

时点规则（19.7 的原文）：只有 realtime_start 不晚于分析日的观测才算数；同一个观测期
（observation date）取其中最晚的一个版本（FRED 的 vintage）。本模块把它落成 chosen_at：
在「观测期不晚于所给月末、版本不晚于分析日」的观测里，取观测期最晚的一个；观测期相同
取 realtime_start 最晚的版本；两个条件有一条不满足的观测根本不进池子——版本晚于分析日
的值永远用不上。发布晚于观测期（realtime_start ≥ date）是诚实档案的常态，于是入选观测
必有 date ≤ as_of，Fact.as_of 不会晚于分析日。

三个变换（MacroSpec.transforms 的字面量）都在同一个月末上取值，单位与序列本身相同：
    level   序列在该月末的值；
    chg_3m  该月末的值减去 3 个月前那个月末的值；
    yoy     该月末的值减去 12 个月前那个月末的值。
缺所需观测时返回 None，绝不拿 0 顶替。chg_3m 与 yoy 的差值口径见 data/macro.toml 的说明。
"""
from __future__ import annotations

import calendar
import datetime
from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from pydantic import FiniteFloat

from .types import Contract, Fact, Unavailable


class Observation(Contract):               # 端口层：一个观测值的一个版本
    date: datetime.date                    # 观测期
    value: FiniteFloat
    realtime_start: datetime.date          # 这个版本开始生效的日期（FRED vintage）


class Series(Contract):
    alias: str                             # data/macro.toml 的表名，例如 "fed_funds"
    source: str
    observations: tuple[Observation, ...]


class Macro(Protocol):
    def series(self, alias: str, as_of: datetime.date) -> Series: ...   # NotCovered / SourceUnavailable / NotConfigured


class MacroSpec(Contract):                 # data/macro.toml 一张表；adapters/declarations_macro.py 读入
    alias: str
    fred_id: str
    label: str
    unit: Literal["pct", "index", "level"]
    transforms: tuple[Literal["level", "chg_3m", "yoy"], ...]


class MarketEntry(Contract):               # 板键 macro/markets 的元素；来源原文，不是事实
    id: str                                # "P1"……
    question: str
    probability: float                     # 来源给的隐含概率，0 到 1，照录不核
    volume: float
    end_date: str
    fetched_on: str


def end_of_month(day: datetime.date) -> datetime.date:
    """day 所在月的最后一天。"""
    return datetime.date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def end_of_month_before(day: datetime.date, months: int) -> datetime.date:
    """day 所在月往前数 months 个月的那个月的最后一天（月末对月末）。"""
    idx = day.year * 12 + day.month - 1 - months
    year, month0 = divmod(idx, 12)
    return datetime.date(year, month0 + 1, calendar.monthrange(year, month0 + 1)[1])


def chosen_at(observations: Sequence[Observation], month_end: datetime.date,
              as_of: datetime.date) -> Observation | None:
    """时点选择：观测期 ≤ month_end 且 realtime_start ≤ as_of 的观测里，观测期最晚、
    同观测期版本最晚的一个。month_end 按该月最后一天给（end_of_month 可代算）。
    """
    chosen: Observation | None = None
    for obs in observations:
        if obs.realtime_start > as_of or obs.date > month_end:
            continue
        if (chosen is None or obs.date > chosen.date
                or (obs.date == chosen.date and obs.realtime_start > chosen.realtime_start)):
            chosen = obs
    return chosen


def value_at(observations: Sequence[Observation], month_end: datetime.date,
             as_of: datetime.date) -> float | None:
    """序列在 month_end、截至 as_of 的值；没有合格的观测时为 None。"""
    chosen = chosen_at(observations, month_end, as_of)
    return None if chosen is None else chosen.value


def level(observations: Sequence[Observation], *,
          month_end: datetime.date, as_of: datetime.date) -> float | None:
    return value_at(observations, month_end, as_of)


def chg_3m(observations: Sequence[Observation], *,
           month_end: datetime.date, as_of: datetime.date) -> float | None:
    """3 个月变化：该月末的值减去 3 个月前月末的值；两头缺一头就算不出。"""
    current = value_at(observations, month_end, as_of)
    earlier = value_at(observations, end_of_month_before(month_end, 3), as_of)
    if current is None or earlier is None:
        return None
    return current - earlier


def yoy(observations: Sequence[Observation], *,
        month_end: datetime.date, as_of: datetime.date) -> float | None:
    """同比变化：该月末的值减去 12 个月前月末的值；两头缺一头就算不出。"""
    current = value_at(observations, month_end, as_of)
    earlier = value_at(observations, end_of_month_before(month_end, 12), as_of)
    if current is None or earlier is None:
        return None
    return current - earlier


_TRANSFORMS: dict[str, Callable[..., float | None]] = {"level": level, "chg_3m": chg_3m, "yoy": yoy}


def fact_id(alias: str, transform: str) -> str:
    """事实 ID（19.7）：level 是 macro_<alias>，其余变换是 macro_<alias>_<transform>。"""
    return f"macro_{alias}" if transform == "level" else f"macro_{alias}_{transform}"


def build_macro(series: list[Series], specs: list[MacroSpec],
                as_of: datetime.date) -> tuple[list[Fact], list[Unavailable]]:
    """把每条序列按声明的变换算成 Fact（只用路径 1）；算不出的记一条 Unavailable。

    月末取 as_of 所在月的月末；Fact.as_of 是所用观测的观测期（不晚于分析日）；
    Fact.source 是 "macro.<变换>"。数值的复算（路径 2）由调用方用 domain/macro_sql.py 做。
    """
    facts: list[Fact] = []
    unavailable: list[Unavailable] = []
    by_alias = {s.alias: s for s in series}
    end = end_of_month(as_of)
    for spec in specs:
        data = by_alias.get(spec.alias)
        for transform in spec.transforms:
            ident = fact_id(spec.alias, transform)
            if data is None:
                unavailable.append(Unavailable(id=ident, reason=f"没有读到序列 {spec.alias} 的宏观数据"))
                continue
            chosen = chosen_at(data.observations, end, as_of)
            value = _TRANSFORMS[transform](data.observations, month_end=end, as_of=as_of)
            if chosen is None or value is None:
                unavailable.append(Unavailable(id=ident, reason=_why_none(transform)))
                continue
            facts.append(Fact(
                id=ident,
                label=_label(spec.label, transform),
                value=value,
                unit=_unit(spec.unit),
                as_of=chosen.date.isoformat(),
                source=f"macro.{transform}",
                bars_used=0))
    return facts, unavailable


def _label(label: str, transform: str) -> str:
    return {"level": label, "chg_3m": f"{label}（3 个月变化）", "yoy": f"{label}（同比变化）"}[transform]


def _unit(unit: str) -> str:
    return {"pct": "%", "index": "index", "level": ""}[unit]


def _why_none(transform: str) -> str:
    return {"level": "截至分析日没有任何已发布的观测",
            "chg_3m": "缺当前值或 3 个月前的观测，算不出 3 个月变化",
            "yoy": "缺当前值或 12 个月前的观测，算不出同比变化"}[transform]
