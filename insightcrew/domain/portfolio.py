# Adapted from TradingAgents (Apache-2.0), modified.
"""组合：调用方账上的现金、权益与持仓（BLUEPRINT 19.7、19.9）。

三态必须区分，这个想法取自 TradingAgents 的 portfolio.py：持有、空仓、没有提供组合。
把“没有提供”当成“空仓”会捏造调用方账上的事实，所以 not_provided 单独一态。
货币是标签，从不换算：组合货币与标的货币不一致时是 unavailable，而不是猜一个汇率。
持仓的形状（代码、数量、平均成本）同样取自那里；空头、换算与其余一切都是本项目的取舍。
组合文件由 adapters/portfolio_json.py 读入；本模块只有契约与纯函数，没有任何 I/O。
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import FiniteFloat

from .types import Contract


class Position(Contract):
    ticker: str
    quantity: FiniteFloat                  # 正数为多头；本版本不支持空头，负数在读取时拒绝
    average_price: FiniteFloat


class Portfolio(Contract):
    currency: str
    cash: FiniteFloat
    equity: FiniteFloat                    # 总权益，> 0
    positions: tuple[Position, ...]


class PortfolioState(Contract):            # 板键 portfolio/state，由代码写
    state: Literal["not_provided", "flat", "held", "unavailable"]
    currency: str | None
    quantity: float | None
    position_pct: float | None             # 持仓市值 / 权益 × 100，按 ledger 的 close
    cash_pct: float | None                 # 现金 / 权益 × 100
    reason: str                            # 一句中文；held 与 flat 时为空


def state_for(portfolio: Portfolio | None, ticker: str, close: float,
              currency: str) -> PortfolioState:
    """由代码把组合折成对某个标的的持仓状态（板键 portfolio/state，BLUEPRINT 19.7）。

    四种情况：没有组合是 not_provided；组合货币与标的货币不同是 unavailable（不换算，
    货币按原样比较）；组合里没有这个标的是 flat；有是 held。held 的 position_pct 按
    ledger 给的 close 计算，不用文件里的平均成本；同一代码出现多条持仓时取第一条。
    代码比较是精确比较：组合文件必须按系统的规范代码书写（例如 "ACME"、"0700.HK"）。
    """
    if portfolio is None:
        return PortfolioState(state="not_provided", currency=None, quantity=None,
                              position_pct=None, cash_pct=None,
                              reason="本次运行没有提供组合")
    if portfolio.currency != currency:
        return PortfolioState(state="unavailable", currency=portfolio.currency, quantity=None,
                              position_pct=None, cash_pct=None,
                              reason=f"组合货币为 {portfolio.currency}，与标的货币 {currency} 不同，不做换算")
    cash_pct = portfolio.cash / portfolio.equity * 100.0
    for position in portfolio.positions:
        if position.ticker == ticker:
            return PortfolioState(state="held", currency=portfolio.currency,
                                  quantity=position.quantity,
                                  position_pct=position.quantity * close / portfolio.equity * 100.0,
                                  cash_pct=cash_pct, reason="")
    return PortfolioState(state="flat", currency=portfolio.currency, quantity=0.0,
                          position_pct=0.0, cash_pct=cash_pct, reason="")


def fingerprint(portfolio: Portfolio | None) -> str:
    """组合的指纹：规范 JSON（键排序、紧凑分隔符）的 SHA-256 前 16 个十六进制位。

    None 时为 "none"。同一份输入永远同一指纹：resume 用它认出自己的检查点，
    组合换了指纹就变（BLUEPRINT 19.5 第 6 条），不会在别人的账上继续跑。
    """
    if portfolio is None:
        return "none"
    canonical = json.dumps(portfolio.model_dump(), sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
