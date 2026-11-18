"""一致性评测（蓝图 12.3 的 `consistency`）：indicators.toml、路径 1 与路径 2 三处互相印证。

对 data/market/synthetic 宇宙里的每个标的，每隔 10 个交易日取一个分析日（另加每个序列
的最后一个交易日）：在同一点上用 history(as_of) 建账本（路径 1），再用 recompute_ledger
（路径 2）复算同一批 bar，disagreements 为空即该点一致。一个标的最少也有 4 个分析日，
全宇宙合计 200 多个数据点，远超蓝图 12.3 的"至少 20 个日期"。与 tests/test_ledger.py
的覆盖同一形状，但作为评测打印结论而不是断言；发现任何一处不一致就退出码 1，
一处不一致都没有才退出 0。
"""
from __future__ import annotations

import datetime
import tomllib
from pathlib import Path

from ..adapters.declarations import load_indicators
from ..adapters.market_csv import CsvMarket
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import Bar

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
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
