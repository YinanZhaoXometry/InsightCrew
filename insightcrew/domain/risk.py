"""风险辩论的输出契约（BLUEPRINT 19.10）。

三位风控（激进、中立、保守）在审计之前评审交易员的方案，辩论只能通过交易员的修订
改变方案。三位共用一个输出契约，分别写入 risk/aggressive、risk/neutral、
risk/conservative 板键；对应的角色声明由 WP31 加入 roles/__init__.py。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, FiniteFloat

from .types import Contract


class RiskView(Contract):
    """一位风控对交易员方案的意见：批评、可选的修改建议，以及对另外两位的回应。"""

    stance: Literal["aggressive", "neutral", "conservative"]
    critique: str
    size_pct: FiniteFloat | None = Field(description="建议的仓位百分比；不建议改动时为 null")
    stop: FiniteFloat | None
    target: FiniteFloat | None
    cites: tuple[str, ...] = Field(description="支撑意见的事实 ID")
    rebuttal: str = Field(description="对另外两位上一轮意见的回应；尚未发言时写空字符串")
