"""一致性评测（蓝图 12.3 的 `consistency`，18.13 加了基本面块）：
indicators.toml 与 fundamentals.toml 各自的两条路径互相印证。

行情块：对 data/market/synthetic 宇宙里的每个标的，每隔 10 个交易日取一个分析日
（另加每个序列的最后一个交易日）：在同一点上用 history(as_of) 建账本（路径 1），
再用 recompute_ledger（路径 2）复算同一批 bar，disagreements 为空即该点一致。
基本面块（18.13）：对每个有年报数据的标的，在同一串分析日上用 build_fundamentals
（路径 1）与 recompute_fundamentals（路径 2）互相印证。发现任何一处不一致就退出码 1，
一处不一致都没有才退出 0。
"""
from __future__ import annotations

import datetime
import tomllib
from pathlib import Path

from ..adapters.declarations import load_fundamentals, load_indicators
from ..adapters.market_csv import CsvMarket
from ..adapters.statements_json import JsonStatements
from ..domain.fundamentals_ledger import build_fundamentals, recompute_fundamentals
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import Bar, NotCovered

_UNIVERSE = "synthetic"
_FAR_FUTURE = datetime.date(2099, 1, 1)   # 取整段序列用：晚于任何数据日期
_STEP = 10                                # 每隔 _STEP 个交易日取一个分析日


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


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
