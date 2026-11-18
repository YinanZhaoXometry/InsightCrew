"""时点评测（蓝图 12.3 的 `pit`）：加新 K 线不改变任何事实，也没有事实晚于分析日。

对 ACME、0000.HK、GAPPY 三个标的，各在自己的序列上均匀取 6 个分析日（含首末两个
交易日）：在同一点上，history(as_of) 建出的账本与把整段序列手工掐到 as_of 的账本，
事实集合必须相同，事实取值必须完全相等（两份输入逐根相同，浮点直接 ==，不走容差）；
同时两条账本里每条事实的 as_of 都不得晚于分析日。任何一处违规退出码 1，
一处违规都没有才退出 0。
"""
from __future__ import annotations

import datetime
from pathlib import Path

from ..adapters.declarations import load_indicators
from ..adapters.market_csv import CsvMarket
from ..domain.ledger import build_ledger
from ..domain.market import Bar
from ..domain.types import Fact

_UNIVERSE = "synthetic"
_FAR_FUTURE = datetime.date(2099, 1, 1)   # 取整段序列用：晚于任何数据日期
_TICKERS = ("ACME", "0000.HK", "GAPPY")   # 蓝图点名的三个标的
_DATES = 6                                # 每个标的的分析日个数（含首末两个交易日）


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
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
