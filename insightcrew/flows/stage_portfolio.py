"""组合阶段（BLUEPRINT 19.9）：把调用方的组合文件折成对本次标的的持仓状态。

在 ledger 之后运行：给了组合文件就写 portfolio/input（组合原件）与 portfolio/state
（货币不匹配记 unavailable，19.7 的四态由 state_for 给出）；没有组合文件时图根本
不调用本阶段（能力关 = 一个键不写，19.3）。审计（stages.audit）从这里取
position_pct 与 cash_pct 交给规则评估；重放时 wiring 用日志里的组合原件替换端口
（决策 44），本阶段重跑写出与录制时相同的两个键。
"""
from __future__ import annotations

from ..domain.market import Instrument
from ..domain.portfolio import state_for
from ..domain.types import Fact, Scope
from ..flows.deps import Deps
from ..kernel.runtime import Run


def portfolio(run: Run, deps: Deps) -> None:
    """写 portfolio/input（组合原件）与 portfolio/state（对该标的的四态折算）。

    调用条件由图的 ledger 节点保证：deps.portfolio 不是 None。close 取账本的
    close 事实：held 的 position_pct 按市价折算，不用文件里的平均成本（19.7）。
    """
    scope = run.board.get("request/scope", Scope)
    instrument = run.board.get("ledger/instrument", Instrument)
    close = next((f.value for f in run.board.get("ledger/facts", list[Fact]) if f.id == "close"), 0.0)
    run.board.put("portfolio/input", deps.portfolio, by="portfolio")
    run.board.put("portfolio/state",
                  state_for(deps.portfolio, scope.ticker, close, instrument.currency),
                  by="portfolio")
