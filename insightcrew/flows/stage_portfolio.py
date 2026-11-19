"""组合阶段（BLUEPRINT 19.9）。WP31 留桩：签名即最终签名，WP32 填全（场景与细化）。

桩先做最小而正确的事：给了组合文件就写 portfolio/input 与 portfolio/state
（货币不匹配记 unavailable）；没有组合文件时图根本不调用本阶段（能力关 = 一个键不写）。
"""
from __future__ import annotations

from ..domain.market import Instrument
from ..domain.portfolio import state_for
from ..domain.types import Fact, Scope
from ..flows.deps import Deps
from ..kernel.runtime import Run


def portfolio(run: Run, deps: Deps) -> None:
    """在 ledger 之后运行：写 portfolio/input（组合原件）与 portfolio/state。

    WP32 填全：场景文件、审计路由到 manager 的组合相关返工等。
    """
    scope = run.board.get("request/scope", Scope)
    instrument = run.board.get("ledger/instrument", Instrument)
    close = next((f.value for f in run.board.get("ledger/facts", list[Fact]) if f.id == "close"), 0.0)
    run.board.put("portfolio/input", deps.portfolio, by="portfolio")
    run.board.put("portfolio/state",
                  state_for(deps.portfolio, scope.ticker, close, instrument.currency),
                  by="portfolio")
