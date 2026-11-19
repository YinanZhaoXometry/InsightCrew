"""新闻与宏观的证据阶段（BLUEPRINT 19.12）。WP34 的正文。

news 通道同时覆盖新闻条目与宏观序列（macro 需要点名 news，prepare 处拒绝只开 macro）。
读不到绝不停下运行（决策 35）：不可用就写明原因，继续；唯一的例外是宏观两条
计算路径的分歧——那是代码的 bug，记一条 evidence 闸门事件后停机核对（与账本、
基本面同一判法）。markets 档案（19.8）只在 fetched_on 恰为分析日那天读，读作
来源原文（MarketEntry，P 加数字），不进事实、不经复算；文件缺失或对不上日就
一个键都不写。能力关 = 一个键都不写（19.8 第 4 条）。
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from ..domain import macro, macro_sql
from ..domain.ledger import disagreements
from ..domain.macro import MarketEntry
from ..domain.market import NotConfigured, NotCovered, SourceUnavailable
from ..domain.news import NewsCard, NewsEntry
from ..domain.types import ChannelStatus, Scope
from ..flows.deps import Deps
from ..kernel.runtime import Halt, Run

# markets 档案在数据目录下：data/market/<universe>/markets/<YYYY-MM-DD>.json（19.8）。
# flows 不能 import adapters（没有 markets 端口可走），所以在本阶段就地读 JSON、
# 用 domain.macro.MarketEntry 校验；根目录与 adapters/wiring 的算法一致。
_DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "market"


def evidence(run: Run, deps: Deps, channels: tuple[str, ...]) -> None:
    """读新闻窗口与宏观序列，写 news/items、news/card、news/status、macro/facts、
    macro/card、macro/markets、macro/status。

    窗口 [as_of − deps.news_window_days, as_of] 两端含（19.8 第 2 条）；条目按发布日
    新到旧排序后截到 deps.news_max_items，编号 N1 起（N1 最新）；无日期的条目由端口
    丢弃并计数。宏观按 deps.macro_specs 逐条序列读取，build_macro（路径 1）产事实，
    macro_sql（路径 2）复算，分歧停机。
    """
    scope = run.board.get("request/scope", Scope)
    as_of = date.fromisoformat(scope.as_of)
    _news(run, deps, scope.ticker, as_of)
    if "macro" in channels:
        _macro(run, deps, as_of)


def _news(run: Run, deps: Deps, ticker: str, as_of: date) -> None:
    """新闻条目：窗口、排序、截断、编号，写三个键；读不到只写状态。"""
    if deps.news is None:
        run.board.put("news/status",
                      ChannelStatus(channel="news", state="unavailable", reason="尚未接入新闻数据源"),
                      by="evidence")
        return
    start = as_of - timedelta(days=deps.news_window_days)
    try:
        fetched = deps.news.headlines(ticker, start, as_of)
    except NotCovered as e:
        _off(run, "news", f"该窗口未覆盖：{e}")
        return
    except SourceUnavailable as e:
        _off(run, "news", str(e))
        return
    ordered = sorted(fetched.items, key=lambda h: (-h.published.toordinal(), h.url))
    kept = ordered[:deps.news_max_items]
    items = [NewsEntry(id=f"N{i}", published=h.published.isoformat(), source=h.source,
                       title=h.title, url=h.url)
             for i, h in enumerate(kept, start=1)]
    run.board.put("news/items", items, by="evidence")
    run.board.put("news/card", NewsCard(
        source=fetched.source, window_from=start.isoformat(), window_to=as_of.isoformat(),
        items=len(items), dropped=fetched.dropped,
        truncated=max(0, len(ordered) - len(kept))), by="evidence")
    run.board.put("news/status",
                  ChannelStatus(channel="news", state="available" if items else "empty",
                                reason="" if items else
                                f"窗口 {start.isoformat()} 到 {as_of.isoformat()} 内没有新闻"),
                  by="evidence")


def _macro(run: Run, deps: Deps, as_of: date) -> None:
    """宏观序列：逐条读取，两条路径算一致才写事实；预测市场同读为来源原文。"""
    if deps.macro is None:
        _off(run, "macro", "尚未接入宏观数据源")
        return
    specs = list(deps.macro_specs)
    if not specs:
        _off(run, "macro", "未声明任何宏观序列（[macro] series 为空）")
        return
    series: list[macro.Series] = []
    failures: list[str] = []
    for spec in specs:
        try:
            series.append(deps.macro.series(spec.alias, as_of))
        except (NotConfigured, NotCovered, SourceUnavailable) as e:
            failures.append(f"{spec.alias}：{e}")
    if failures:
        # 声明的序列是一条整体契约：有一条读不到就不冒充可用，逐条写明原因。
        _off(run, "macro", "；".join(failures))
        return
    facts, unavailable = macro.build_macro(series, specs, as_of)
    differing = disagreements(facts, _recompute(series, specs, as_of))
    if differing:
        detail = [{"check": "ledger_inconsistent", "subject": fact_id,
                   "message": "两条宏观计算路径给出的结果不一致"} for fact_id in differing]
        run.journal.append("gate.checked", actor="evidence",
                           data={"gate": "evidence", "findings": len(detail), "detail": detail})
        raise Halt("ledger_inconsistent", "、".join(f"macro/{f}" for f in differing))
    run.board.put("macro/facts", facts, by="evidence")
    run.board.put("macro/card", {
        "source": "、".join(sorted({s.source for s in series})),
        "series": [s.alias for s in series],
        "unavailable": unavailable}, by="evidence")
    markets = _markets(deps.universe, as_of)
    if markets:
        run.board.put("macro/markets", markets, by="evidence")
    run.board.put("macro/status",
                  ChannelStatus(channel="macro", state="available" if facts else "empty",
                                reason="" if facts else "读到了宏观数据，但截至分析日算不出任何宏观事实"),
                  by="evidence")


def _recompute(series: list[macro.Series], specs: list[macro.MacroSpec],
               as_of: date) -> dict[str, float | None]:
    """路径 2（macro_sql，SQL）按声明的变换逐条复算，键是事实 ID。"""
    end = macro.end_of_month(as_of)
    by_alias = {s.alias: s for s in series}
    out: dict[str, float | None] = {}
    for spec in specs:
        data = by_alias.get(spec.alias)
        if data is None:
            continue
        with closing(macro_sql.load(data.observations)) as db:
            for transform in spec.transforms:
                out[macro.fact_id(spec.alias, transform)] = getattr(macro_sql, transform)(
                    db, month_end=end, as_of=as_of)
    return out


def _markets(universe: str, as_of: date) -> list[MarketEntry]:
    """预测市场档案（19.8）：只在 fetched_on 恰为分析日那天读；文件缺失、读不出、
    对不上日都视作没有行情，一个键都不写（markets 是来源原文，不是事实，缺了不碍事）。"""
    path = _DATA_ROOT / universe / "markets" / f"{as_of.isoformat()}.json"
    if not path.exists():
        return []
    try:
        file = json.loads(path.read_text(encoding="utf-8"))
        fetched_on = str(file["fetched_on"])
        if fetched_on != as_of.isoformat():
            return []
        rows = file["markets"]
        return [MarketEntry(id=f"P{i}", question=str(row["question"]),
                            probability=float(row["probability"]), volume=float(row["volume"]),
                            end_date=str(row["end_date"]), fetched_on=fetched_on)
                for i, row in enumerate(rows, start=1)]
    except (OSError, KeyError, TypeError, ValueError):
        # ValueError 已含 JSON 解析错误与 MarketEntry 的契约校验错误（其子类）。
        return []


def _off(run: Run, channel: Literal["news", "macro"], reason: str) -> None:
    """通道不可用：只写状态与原因，此外一个键都不写（19.8 第 4 条）。"""
    run.board.put(f"{channel}/status",
                  ChannelStatus(channel=channel, state="unavailable", reason=reason), by="evidence")
