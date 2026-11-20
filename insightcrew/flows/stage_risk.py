# Adapted from TradingAgents (Apache-2.0), modified.
"""三方风险辩论（BLUEPRINT 19.10）。“先改善方案、再审它”的辩论段取自 TradingAgents
的风险辩论：激进、保守、中立依次对交易员的方案提意见，交易员听完修订一次；
对话的走向（谁先说、谁能改方案）照搬，意见的形状与约束重写为本项目的 RiskView
契约与黑板键。辩论只能通过交易员的修订改变方案（决策 45）：本阶段自己不写任何板键，
三位风控与交易员的输出各自由 run_role 写到 risk/<立场> 与 trader/proposal。

risk 节点只在 FlowConfig.risk_rounds > 0 时加入图，在 trader 之后、audit 之前。
审计打回到 trader 时，risk 在它之后重跑（图的线性边 trader -> risk -> audit 已保证）。

版本 2.3（BLUEPRINT 20.7 第 1 条，修正 D1）：risk 是异步函数，直接 await 整段辩论。
19.10 曾把同步签名视为最终签名，本条修订之（HANDOFF 问题 34）。原先的线程桥
（另起一线程跑一个新事件循环）会阻塞服务器的事件循环整段辩论——流不再前进、
运行无法取消，模型的 HTTP 客户端还会被两条循环使用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.runtime import Role, Run
from ..roles import RISK_AGGRESSIVE, RISK_CONSERVATIVE, RISK_NEUTRAL, TRADER
from .analyze import call
from .deps import Deps

if TYPE_CHECKING:
    from .analyze import FlowConfig  # 仅类型引用：避免运行时的模块加载环

# 每轮的发言顺序（19.10 原文）：激进、保守、中立；声明顺序（roles/__init__）与此不同。
_DEBATERS: tuple[Role, ...] = (RISK_AGGRESSIVE, RISK_CONSERVATIVE, RISK_NEUTRAL)


async def _debate(run: Run, cfg: FlowConfig) -> None:
    """按轮数辩论：每轮三位风控依次发言，然后交易员据三方意见修订一次方案。"""
    for _ in range(cfg.risk_rounds):
        for role in _DEBATERS:
            await call(run, role)
        await call(run, TRADER)


async def risk(run: Run, deps: Deps, cfg: FlowConfig) -> None:
    """跑完整个辩论（WP32 正文；版本 2.3 异步化）。deps 目前用不到：辩论只读黑板。"""
    await _debate(run, cfg)
