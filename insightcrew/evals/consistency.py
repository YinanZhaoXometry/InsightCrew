"""一致性评测（蓝图 12.3 的 `consistency`，18.13 加了基本面块，19.16 加了版本 2.2 块）：
indicators.toml 与 fundamentals.toml 各自的两条路径互相印证。

行情块：对 data/market/synthetic 宇宙里的每个标的，每隔 10 个交易日取一个分析日
（另加每个序列的最后一个交易日）：在同一点上用 history(as_of) 建账本（路径 1），
再用 recompute_ledger（路径 2）复算同一批 bar，disagreements 为空即该点一致。
基本面块（18.13）：对每个有年报数据的标的，在同一串分析日上用 build_fundamentals
（路径 1）与 recompute_fundamentals（路径 2）互相印证。
工具块（19.16，不变量 21）：每个工具家族在若干窗口上经 flows/tools.py 调用
（它内部已把 domain/indicators 与 domain/recompute 两条路径各算一遍并比对），
再把工具交回的数值与 domain/recompute 的独立复算互相比对。
宏观块（19.16，不变量 4）：每个宏观序列的每个变换在若干分析日上用
domain/macro.py（路径 1）与 domain/macro_sql.py（路径 2）互相印证。
发现任何一处不一致就退出码 1，一处不一致都没有才退出 0。
"""
from __future__ import annotations

import datetime
import math
import tomllib
from contextlib import closing
from pathlib import Path
from typing import Any

from ..adapters.declarations import load_fundamentals, load_indicators
from ..adapters.declarations_macro import load_macro
from ..adapters.declarations_tools import load_tool_indicators
from ..adapters.macro_json import JsonMacro
from ..adapters.market_csv import CsvMarket
from ..adapters.scripted import Script
from ..adapters.statements_json import JsonStatements
from ..domain import macro, macro_sql, recompute
from ..domain.fundamentals_ledger import build_fundamentals, recompute_fundamentals
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.macro import end_of_month
from ..domain.market import Bar, NotCovered
from ..domain.types import Fact, Scope
from ..flows.deps import Deps
from ..flows.tools import build_tools
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Journal
from ..kernel.runtime import Halt, Run

_UNIVERSE = "synthetic"
_FAR_FUTURE = datetime.date(2099, 1, 1)   # 取整段序列用：晚于任何数据日期
_STEP = 10                                # 每隔 _STEP 个交易日取一个分析日

# ---- 版本 2.2 块的常量 ----

_TOOL_TICKER = "ACME"                     # 工具块用的标的（与 WP26 的验收同一标的）
_TOOL_DATES = (datetime.date(2026, 3, 31), datetime.date(2026, 6, 30),
               datetime.date(2026, 9, 30))   # 工具块的分析日：早、中、账本最后一根
_MACRO_DATES = (datetime.date(2025, 3, 31), datetime.date(2025, 6, 30),
                datetime.date(2025, 9, 30), datetime.date(2025, 12, 31),
                datetime.date(2026, 3, 11), datetime.date(2026, 3, 12),
                datetime.date(2026, 6, 30), datetime.date(2026, 9, 30))   # 月末 + cpi 重述两侧


def _agree(one: float | None, two: float | None) -> bool:
    """与账本同一读法（math.isclose 双重容差）：两边都算不出算一致；只有一边算得出算不一致。"""
    if one is None or two is None:
        return one is None and two is None
    return math.isclose(one, two, rel_tol=1e-6, abs_tol=1e-9)


def _tickers(path: Path) -> list[str]:
    """宇宙的全部代码：universe.toml 的表名就是代码（adapters/market_csv 的装载约定）。"""
    with path.open("rb") as f:
        tables = tomllib.load(f)
    return list(tables)


def _sweep_dates(full: tuple[Bar, ...]) -> list[datetime.date]:
    """一个序列的分析日扫掠：每隔 _STEP 个交易日取一个，另加最后一个交易日（若不在其列）。"""
    dates = [full[i].date for i in range(_STEP - 1, len(full), _STEP)]
    if dates[-1] != full[-1].date:
        dates.append(full[-1].date)
    return dates


def main(root: Path) -> int:
    """跑一致性评测：全部一致打印一行结论并退出 0，有不一致先逐条列出再退出 1。"""
    specs = load_indicators(root / "data" / "indicators.toml")
    market = CsvMarket(root / "data" / "market", _UNIVERSE)
    tickers = _tickers(root / "data" / "market" / _UNIVERSE / "universe.toml")
    declared = sum(len(spec.outputs) for spec in specs)   # 声明的事实 ID 总数
    points = 0
    problems: list[str] = []
    for ticker in tickers:
        instrument = market.instrument(ticker)
        dropped = market.dropped_rows(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _sweep_dates(full):
            bars = market.history(ticker, as_of)
            facts, _card = build_ledger(instrument, bars, as_of, specs, dropped)
            bad = disagreements(facts, recompute_ledger(instrument, bars, as_of, specs))
            points += 1
            if bad:
                problems.append(f"{ticker} @ {as_of.isoformat()}：{'、'.join(bad)}")
    for line in problems:
        print(f"不一致：{line}")
    verdict = "全部一致" if not problems else f"{len(problems)} 处不一致"
    print(f"一致性评测：{points} 个数据点 × {declared} 项声明，{verdict}")

    _fundamentals_block(root, market, tickers, problems)
    _tools_block(root, problems)
    _macro_block(root, problems)
    return 0 if not problems else 1


def _fundamentals_block(root: Path, market: CsvMarket, tickers: list[str],
                        problems: list[str]) -> int:
    """基本面块（BLUEPRINT 18.13）：对每个有年报数据的标的，两条路径在扫掠的日期上互相印证。

    没有年报文件的标的（端口以 NotCovered 回答）自然一个点都不贡献。
    """
    specs = load_fundamentals(root / "data" / "fundamentals.toml")
    port = JsonStatements(root / "data" / "market", _UNIVERSE)
    points = 0
    for ticker in tickers:
        instrument = market.instrument(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _sweep_dates(full):
            close = market.history(ticker, as_of)[-1].close
            try:
                ss = port.statements(ticker, as_of)
            except NotCovered:                              # 没有年报文件，或分析日晚于其覆盖
                continue
            if not ss.rows:
                continue
            facts, _card = build_fundamentals(instrument, ss, close, as_of, as_of, specs)
            bad = disagreements(facts, recompute_fundamentals(ss, close, as_of, specs))
            points += 1
            if bad:
                problems.append(f"{ticker} 基本面 @ {as_of.isoformat()}：{'、'.join(bad)}")
    for line in [p for p in problems if "基本面" in p]:
        print(f"不一致：{line}")
    verdict = "全部一致" if not any("基本面" in p for p in problems) else "存在不一致"
    print(f"基本面一致性评测：{points} 个数据点 × {len(specs)} 项声明，{verdict}")
    return points


# ---- 版本 2.2 块（BLUEPRINT 19.16）：工具家族与宏观变换，各经两条路径 ----


def _tools_run(as_of: datetime.date) -> Run:
    """一个只带 request/scope 的运行：工具在 intake 之后、任何模型调用之前构建。"""
    journal = Journal()
    run = Run(id="consistency", journal=journal, board=Board(journal), models=Script({}),
              budget=Budget())
    run.board.put("request/scope", Scope(ticker=_TOOL_TICKER, as_of=as_of.isoformat(),
                                         universe=_UNIVERSE, requested=_TOOL_TICKER),
                  by="intake")
    return run


def _windows(spec: Any) -> list[int]:
    """一个家族的窗口扫掠：范围下限、上限，另加不超过 20 的一个中庸窗口。"""
    return sorted({spec.min, min(20, spec.max), spec.max})


def _tools_block(root: Path, problems: list[str]) -> int:
    """工具块（不变量 21）：每个家族在若干窗口、若干分析日上，flows/tools.py 的计算与
    domain/recompute 的独立复算互相比对。

    工具内部已把 domain/indicators（路径 1）与 domain/recompute（路径 2）各算一遍并
    比对（分歧即停机）；这里再独立复算路径 2，核对工具交回的数值与之一致、
    工具说数据不可用时路径 2 也确实算不出。
    """
    specs = load_tool_indicators(root / "data" / "tool_indicators.toml")
    market = CsvMarket(root / "data" / "market", _UNIVERSE)
    points = 0
    for as_of in _TOOL_DATES:
        point = as_of.isoformat()
        deps = Deps(market=market, indicators=(), rules=(), today=as_of, universe=_UNIVERSE)
        box = build_tools(_tools_run(as_of), deps, specs)
        instrument = market.instrument(_TOOL_TICKER)
        bars = tuple(b for b in market.history(_TOOL_TICKER, as_of) if b.date <= as_of)
        rows = [(instrument.ticker, bar.date.isoformat(), bar.open, bar.high, bar.low,
                 bar.close, bar.volume) for bar in bars]
        for spec in specs:
            for n in _windows(spec):
                points += 1
                try:
                    result: dict[str, Any] | str = box["indicator"](spec.key, n)
                except Halt as e:               # 工具内部的两路比对已经分歧
                    problems.append(f"工具 {spec.key}_{n} @ {point}：两条计算路径分歧"
                                    f"（{e.reason}）")
                    continue
                with closing(recompute.load(rows)) as db:
                    two = getattr(recompute, spec.fn)(db, instrument.ticker, point,
                                                      spec.inputs, **{spec.arg: n})
                if isinstance(result, dict):
                    fact = Fact(id=str(result["id"]), label="", value=float(result["value"]),
                                unit=str(result["unit"]), as_of=str(result["as_of"]),
                                source="eval.tools", bars_used=0)
                    if disagreements([fact], {fact.id: two}):
                        problems.append(f"工具 {spec.key}_{n} @ {point}：flows/tools 与 "
                                        f"domain/recompute 不一致（{fact.value} vs {two}）")
                elif two is not None:
                    problems.append(f"工具 {spec.key}_{n} @ {point}：工具说数据不可用，"
                                    f"domain/recompute 却算出 {two}")
    bad = [p for p in problems if p.startswith("工具 ")]
    for line in bad:
        print(f"不一致：{line}")
    verdict = "全部一致" if not bad else f"{len(bad)} 处不一致"
    print(f"工具一致性评测：{points} 次调用 × 两条路径（{len(specs)} 个家族 × "
          f"{len(_TOOL_DATES)} 个分析日）：{verdict}")
    return points


def _macro_block(root: Path, problems: list[str]) -> int:
    """宏观块（不变量 4）：每条序列的每个变换，domain/macro.py 与 domain/macro_sql.py 互相印证。

    分析日取月末与 cpi 重述（2026-03-12）前后两侧：重述两侧是最容易暴露时点口径
    分歧的地方。两条路径互不共享代码（tests/test_macro.py 强制），这里只比对数值。
    """
    specs = load_macro(root / "data" / "macro.toml")
    port = JsonMacro(root / "data" / "market", _UNIVERSE)
    points = 0
    for as_of in _MACRO_DATES:
        end = end_of_month(as_of)
        for spec in specs:
            try:
                series = port.series(spec.alias, as_of)
            except NotCovered:                  # 分析日早于该序列最早的观测期
                continue
            with closing(macro_sql.load(series.observations)) as db:
                for transform in spec.transforms:
                    points += 1
                    one = getattr(macro, transform)(series.observations, month_end=end,
                                                    as_of=as_of)
                    two = getattr(macro_sql, transform)(db, month_end=end, as_of=as_of)
                    if not _agree(one, two):
                        problems.append(f"宏观 {spec.alias}.{transform} @ {as_of.isoformat()}："
                                        f"两条路径不一致（{one} vs {two}）")
    bad = [p for p in problems if p.startswith("宏观 ")]
    for line in bad:
        print(f"不一致：{line}")
    verdict = "全部一致" if not bad else f"{len(bad)} 处不一致"
    print(f"宏观一致性评测：{points} 个数据点 × 两条路径：{verdict}")
    return points


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
