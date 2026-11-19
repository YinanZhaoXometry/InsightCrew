"""时点评测（蓝图 12.3 的 `pit`，18.13 加了证据通道块）。

行情块：加新 K 线不改变任何事实，也没有事实晚于分析日。
通道块（18.13）：加更晚的申报与更晚的公告，不改变任何更早日期的基本面事实与条目列表；
没有基本面事实的申报日晚于分析日，也没有条目发布晚于分析日。
任何一处违规退出码 1，一处违规都没有才退出 0。
"""
from __future__ import annotations

import dataclasses
import datetime
from pathlib import Path

from ..adapters.declarations import load_fundamentals, load_indicators
from ..adapters.disclosures_json import JsonDisclosures
from ..adapters.market_csv import CsvMarket
from ..adapters.statements_json import JsonStatements
from ..domain.fundamentals_ledger import build_fundamentals
from ..domain.ledger import build_ledger
from ..domain.market import Bar, NotCovered
from ..domain.types import Fact

_UNIVERSE = "synthetic"
_FAR_FUTURE = datetime.date(2099, 1, 1)   # 取整段序列用：晚于任何数据日期
_TICKERS = ("ACME", "0000.HK", "GAPPY")   # 蓝图点名的三个标的
_DATES = 6                                # 每个标的的分析日个数（含首末两个交易日）
_WINDOW_DAYS = 90                         # 公告块的窗口长度，与运行的默认设置一致


def _analysis_dates(full: tuple[Bar, ...]) -> list[datetime.date]:
    """一段序列里均匀取 _DATES 个分析日（都是序列自己的交易日，与 test_ledger 同一取法）。"""
    step = (len(full) - 1) / (_DATES - 1)
    return [full[round(i * step)].date for i in range(_DATES)]


def _violations(ticker: str, as_of: datetime.date, from_history: list[Fact],
                from_cut: list[Fact]) -> list[str]:
    """同一点上的违规：事实集合不同、加入后期 K 线后取值改变、或 as_of 晚于分析日。"""
    point = as_of.isoformat()
    problems: list[str] = []
    history_values = {fact.id: fact.value for fact in from_history}
    cut_values = {fact.id: fact.value for fact in from_cut}
    if tuple(history_values) != tuple(cut_values):
        problems.append(f"{ticker} @ {point}：两份账本的事实集合不同")
    else:
        for fact_id, value in history_values.items():
            if value != cut_values[fact_id]:
                problems.append(f"{ticker} @ {point}：加入后期 K 线后事实 {fact_id} 的取值改变"
                                f"（{value} → {cut_values[fact_id]}）")
    for fact in (*from_history, *from_cut):
        if datetime.date.fromisoformat(fact.as_of) > as_of:
            problems.append(f"{ticker} @ {point}：事实 {fact.id} 的 as_of {fact.as_of}"
                            f" 晚于分析日 {point}")
    return problems


def main(root: Path) -> int:
    """跑时点评测：无违规打印一行结论并退出 0，有违规先逐条列出再退出 1。"""
    specs = load_indicators(root / "data" / "indicators.toml")
    market = CsvMarket(root / "data" / "market", _UNIVERSE)
    points = 0
    compared = 0
    problems: list[str] = []
    for ticker in _TICKERS:
        instrument = market.instrument(ticker)
        dropped = market.dropped_rows(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _analysis_dates(full):
            from_history, _card = build_ledger(instrument, market.history(ticker, as_of), as_of,
                                               specs, dropped)
            cut = tuple(bar for bar in full if bar.date <= as_of)   # 手工掐到分析日（含当天）
            from_cut, _card = build_ledger(instrument, cut, as_of, specs, dropped)
            points += 1
            compared += len(from_history) + len(from_cut)
            problems.extend(_violations(ticker, as_of, from_history, from_cut))
    for line in problems:
        print(f"违规：{line}")
    verdict = ("加新 K 线不改变任何事实，也没有事实晚于分析日" if not problems
               else f"{len(problems)} 处违规")
    print(f"时点评测：{points} 个数据点，比对 {compared} 项事实：{verdict}")
    _channel_block(root, market, problems)
    return 0 if not problems else 1


def _channel_block(root: Path, market: CsvMarket, problems: list[str]) -> int:
    """通道块（18.13）：更晚的申报与更晚的公告，不改变更早日期的基本面事实与条目列表。

    无年报数据的标的（端口以 NotCovered 回答）自然不贡献点。
    """
    specs = load_fundamentals(root / "data" / "fundamentals.toml")
    statements = JsonStatements(root / "data" / "market", _UNIVERSE)
    disclosures = JsonDisclosures(root / "data" / "market", _UNIVERSE)
    points = 0
    for ticker in _TICKERS:
        instrument = market.instrument(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _analysis_dates(full):
            close = market.history(ticker, as_of)[-1].close
            point = as_of.isoformat()
            try:
                ss = statements.statements(ticker, as_of)
                later = statements.statements(ticker, full[-1].date)
            except NotCovered:                          # 没有年报数据：跳过基本面部分
                pass
            else:
                later_cut = dataclasses.replace(
                    later, rows=tuple(row for row in later.rows if row.filed <= as_of))
                facts_now = build_fundamentals(instrument, ss, close, as_of, as_of, specs)[0]
                now = {f.id: f.value for f in facts_now}
                later_facts = {f.id: f.value for f in build_fundamentals(
                    instrument, later_cut, close, as_of, as_of, specs)[0]}
                points += 1
                if tuple(now) != tuple(later_facts):
                    problems.append(f"{ticker} 基本面 @ {point}：加入更晚的申报后事实集合改变")
                else:
                    for fact_id, value in now.items():
                        if value != later_facts[fact_id]:
                            problems.append(f"{ticker} 基本面 @ {point}：加入更晚的申报后"
                                            f"事实 {fact_id} 的取值改变")
                for fact in facts_now:
                    if datetime.date.fromisoformat(fact.as_of) > as_of:
                        problems.append(f"{ticker} 基本面 @ {point}：事实 {fact.id} 的 as_of"
                                        f" {fact.as_of} 晚于分析日")
            try:
                window = disclosures.published(ticker, as_of - datetime.timedelta(days=_WINDOW_DAYS),
                                               as_of)
                later_items = disclosures.published(
                    ticker, as_of - datetime.timedelta(days=_WINDOW_DAYS), full[-1].date)
                later_cut_items = tuple(p for p in later_items.items if p.published <= as_of)
            except NotCovered:
                continue
            points += 1
            if tuple(window.items) != later_cut_items:
                problems.append(f"{ticker} 公告 @ {point}：加入更晚的公告后窗口条目列表改变")
    for line in problems:
        if "基本面 @" in line or "公告 @" in line:
            print(f"违规：{line}")
    bad = [p for p in problems if "基本面 @" in p or "公告 @" in p]
    verdict = ("加更晚的申报与公告不改变任何更早日期的事实或条目，也没有事实晚于分析日"
               if not bad else f"{len(bad)} 处违规")
    print(f"证据通道时点评测：{points} 个数据点：{verdict}")
    return points


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
