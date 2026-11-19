# Adapted from TradingAgents (Apache-2.0), modified.
"""分析工具（蓝图 19.11）：技术分析师在账本之外按窗口请求指标。

build_tools 在构建时绑定标的与分析日（蓝图 19.8 第 5 条：工具永远不接收日期参数），
返回一张名字到工具的映射；目前只有一个工具 indicator(name, n)。

日期的规范化与钳制改编自 TradingAgents 的 date_window.py（as_of / as_of_window）：
分析日取黑板上 request/scope 的 as_of，先规范化成 date，再以零填充的 YYYY-MM-DD
交给路径 2——SQL 里日期按文本比较，"2026-7-3" 这类非规范写法会排出错误的窗口；
K 线在进入任何一条路径之前先钳制到分析日（含），所以行情端口多给的分析日之后的
bar 永远影响不了任何结果。

每个工具事实都经两条路径（domain/indicators.py 与 domain/recompute.py）计算并比对，
分歧时与账本（flows/stages.py 的 ledger 阶段）完全一样地停机：先记一条 gate.checked
事件说明每条不一致的事实，再抛 Halt("ledger_inconsistent")。

工具自己不写任何黑板键：事实按调用顺序收集在返回映射的 .facts 里（ToolBox），
由提交角色输出的节点写上黑板键 tools/facts（WP31/WP33 接线）。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from contextlib import closing
from typing import Any, Literal, Protocol

from ..domain import indicators, recompute
from ..domain.ledger import disagreements
from ..domain.market import Bar, Instrument, NoData, SourceUnavailable, UnknownTicker
from ..domain.types import Fact, Scope
from ..kernel.runtime import Halt, Run
from .deps import Deps


class _ToolSpec(Protocol):
    """工具指标声明的结构约定；实现是 adapters/declarations_tools.py 的 ToolSpec。

    flows 不 import adapters（分层规则），所以按结构接收声明，字段与 ToolSpec 一一对应。
    """

    key: str                      # 家族名，即工具调用里的 name
    fn: str                       # 两条路径里同名的函数
    inputs: tuple[str, ...]       # 输入列，取值 open/high/low/close/volume
    arg: str                      # 窗口参数名
    min: int                      # 窗口参数允许的最小值（含）
    max: int                      # 窗口参数允许的最大值（含）
    unit: Literal["price", "pct", "shares", "none"]   # 与 ToolSpec 同一枚举；price 换计价货币
    label: str                    # 中文名，含窗口占位符


class ToolBox(dict[str, Callable[..., Any]]):
    """build_tools 的返回值：一张名字到工具的映射，附上本次运行收集到的工具事实。

    facts 是按调用顺序收集的事实（同一条不重复收集）；提交角色输出的节点把它写上
    黑板键 tools/facts。工具自己绝不写黑板键。
    """

    def __init__(self, facts: list[dict[str, Any]]):
        super().__init__()
        self.facts = facts


def build_tools(run: Run, deps: Deps, specs: Iterable[_ToolSpec]) -> dict[str, Callable[..., Any]]:
    """构建本次运行的工具；分析日在此绑定，工具从此不接收任何日期（蓝图 19.8 第 5 条）。

    必须在 intake 写下 request/scope 之后调用。specs 是 tool_indicators.toml 装出来的
    声明（adapters/declarations_tools.py）。返回的映射带 .facts（ToolBox）。
    """
    try:
        scope = run.board.get("request/scope", Scope)
    except KeyError as e:
        raise ValueError("build_tools 必须在 intake 写下 request/scope 之后调用") from e
    instrument, as_of, bars = _bound(deps, scope.ticker, scope.as_of)
    rows = [(instrument.ticker, bar.date.isoformat(), bar.open, bar.high, bar.low,
             bar.close, bar.volume) for bar in bars]
    by_family = {spec.key: spec for spec in specs}
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def indicator(name: str, n: int) -> dict[str, Any] | str:
        """这段 docstring 会被 build_tools 换成由声明拼出的工具说明（见 _describe）。"""
        spec = by_family.get(name)
        if spec is None:
            families = "、".join(by_family)
            return f"未知的指标名 {name!r}；可用的家族：{families}。数据不可用，不要编造。"
        if not spec.min <= n <= spec.max:
            return (f"{name} 的窗口 {spec.arg} = {n} 超出允许范围 {spec.min} 到 {spec.max}。"
                    "数据不可用，不要编造。")
        kwargs = {spec.arg: n}
        label = spec.label.format(**kwargs)
        # 路径 1 的调用约定与账本一致：fn(*[series[name] for name in inputs], **args)。
        one = getattr(indicators, spec.fn)(
            *(tuple(getattr(bar, column) for bar in bars) for column in spec.inputs), **kwargs)
        # 路径 2 的调用约定：fn(db, ticker, as_of, inputs, **args)；日期一律给零填充 ISO。
        with closing(recompute.load(rows)) as db:
            two = getattr(recompute, spec.fn)(db, instrument.ticker, as_of.isoformat(),
                                              spec.inputs, **kwargs)
        fact_id = f"{name}_{n}"
        fact: Fact | None = None
        if one is not None:
            fact = Fact(id=fact_id, label=label, value=one,
                        unit=_resolve_unit(spec.unit, instrument),
                        as_of=bars[-1].date.isoformat(), source=f"tools.{spec.fn}",
                        bars_used=len(bars))
        # 与账本同一比较：数值差超出容差、或只在一条路径上算得出来，都算不一致。
        differing = disagreements([fact] if fact else [], {fact_id: two})
        if differing:
            _gate(run, [{"check": "ledger_inconsistent", "subject": subject,
                         "message": "两条计算路径给出的结果不一致"} for subject in differing])
            raise Halt("ledger_inconsistent", "、".join(differing))
        if fact is None:            # 两条路径都算不出：历史不足，如实说明，不编造
            return (f"历史不足：{label} 在现有的 {len(bars)} 根 K 线上算不出来。"
                    "数据不可用，不要编造。")
        result = {"id": fact_id, "value": fact.value, "unit": fact.unit, "as_of": fact.as_of}
        if fact_id not in seen:     # 每条新事实只收集一次，保留首次调用的顺序
            seen.add(fact_id)
            facts.append(result)
        return result

    indicator.__doc__ = _describe(by_family)
    box = ToolBox(facts)
    box["indicator"] = indicator
    return box


def _bound(deps: Deps, ticker: str, as_of_raw: str) -> tuple[Instrument, datetime.date, tuple[Bar, ...]]:
    """绑定标的、分析日与它的 K 线；日期的规范化与钳制改编自 date_window.py 的 as_of。

    分析日先规范化成 date（canonicalise），路径 2 只见它的零填充 ISO 文本；
    K 线在返回前钳制（clamp）到分析日（含）——端口多给的 bar 也进不了任何一条路径。
    """
    as_of = datetime.date.fromisoformat(as_of_raw)
    try:
        instrument = deps.market.instrument(ticker)
        bars = deps.market.history(ticker, as_of)
    except UnknownTicker as e:
        raise Halt("unknown_ticker", str(e)) from e
    except NoData as e:
        raise Halt("no_data", str(e)) from e
    except SourceUnavailable as e:
        raise Halt("source_unavailable", str(e)) from e
    bars = tuple(bar for bar in bars if bar.date <= as_of)
    if not bars:
        raise Halt("no_data", f"{ticker} 在 {as_of.isoformat()}（含）之前没有已结算的 K 线")
    return instrument, as_of, bars


def _resolve_unit(unit: str, instrument: Instrument) -> str:
    """把声明里的单位换算成事实单位：与账本（domain/ledger.py）同一套规则。"""
    if unit == "price":
        return instrument.currency
    if unit == "pct":
        return "%"
    if unit == "shares":
        return "shares"
    return ""                      # "none"：无量纲指标


def _gate(run: Run, detail: list[dict[str, Any]]) -> None:
    """工具闸门事件：与账本闸门同一形状（flows/stages.py 的 _gate 的镜像）。

    拦下运行前先记下原因的细节：为什么停在日志里，而不只是一个原因代号。
    """
    run.journal.append("gate.checked", actor="tools",
                       data={"gate": "tools", "findings": len(detail), "detail": detail})


def _describe(by_family: dict[str, _ToolSpec]) -> str:
    """工具说明；Pydantic AI 把函数的 docstring 当作工具描述发给模型。

    家族与范围从声明拼出，不在这里另写一份：声明是唯一出处。
    """
    families = "；".join(
        f"{spec.key}（{spec.arg} 允许 {spec.min} 到 {spec.max}）" for spec in by_family.values())
    return ("请求一个技术指标在本次分析日的值。数据截止到分析日，本工具不接收日期参数。\n"
            f"name 是指标家族名，可取：{families}。\n"
            "n 是窗口长度，必须是所在家族允许范围内的整数。\n"
            "成功时返回一个对象：id（事实 ID，引用这个数时写它）、value（数值）、"
            "unit（单位）与 as_of（数值所属的交易日）；\n"
            "名字未知、窗口超出范围或历史不足时，返回一句以「数据不可用，不要编造。」结尾的"
            "错误说明——此时不要引用任何数值，更不要编造。")
