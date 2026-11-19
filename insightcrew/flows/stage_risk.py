# Adapted from TradingAgents (Apache-2.0), modified.
"""三方风险辩论（BLUEPRINT 19.10）。“先改善方案、再审它”的辩论段取自 TradingAgents
的风险辩论：激进、保守、中立依次对交易员的方案提意见，交易员听完修订一次；
对话的走向（谁先说、谁能改方案）照搬，意见的形状与约束重写为本项目的 RiskView
契约与黑板键。辩论只能通过交易员的修订改变方案（决策 45）：本阶段自己不写任何板键，
三位风控与交易员的输出各自由 run_role 写到 risk/<立场> 与 trader/proposal。

risk 节点只在 FlowConfig.risk_rounds > 0 时加入图，在 trader 之后、audit 之前。
审计打回到 trader 时，risk 在它之后重跑（图的线性边 trader -> risk -> audit 已保证）。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from ..kernel.runtime import Halt, Malformed, Role, Run, run_role
from ..roles import RISK_AGGRESSIVE, RISK_CONSERVATIVE, RISK_NEUTRAL, TRADER
from .deps import Deps

if TYPE_CHECKING:
    from .analyze import FlowConfig  # 仅类型引用：analyze 已引入 graph，避免加载环

# 每轮的发言顺序（19.10 原文）：激进、保守、中立；声明顺序（roles/__init__）与此不同。
_DEBATERS: tuple[Role, ...] = (RISK_AGGRESSIVE, RISK_CONSERVATIVE, RISK_NEUTRAL)


async def _call(run: Run, role: Role) -> None:
    """调用一个角色；两次不合契约即停机。与 flows.analyze.call 同一条规则（图内另有一份）。"""
    try:
        await run_role(run, role)
    except Malformed as e:
        raise Halt("malformed_output", str(e)) from e


async def _debate(run: Run, cfg: FlowConfig) -> None:
    """按轮数辩论：每轮三位风控依次发言，然后交易员据三方意见修订一次方案。"""
    for _ in range(cfg.risk_rounds):
        for role in _DEBATERS:
            await _call(run, role)
        await _call(run, TRADER)


def risk(run: Run, deps: Deps, cfg: FlowConfig) -> None:
    """跑完整个辩论（WP32 正文）。签名是 WP31 定下的最终签名：同步。

    内核的 run_role 是协程，而图的 risk 节点在事件循环线程里同步调用本函数，
    所以这里按调用环境搭桥：不在事件循环里（直接调用、单元测试）就 asyncio.run；
    在事件循环里（图的节点）就把整段辩论放进单独线程的新循环里跑完再返回——
    主线程此刻阻塞在本函数里，黑板与日志没有并发访问（Journal 自带锁），
    异常（含 Halt）原样带回调用方。deps 目前用不到：辩论只读黑板。
    """
    coro = _debate(run, cfg)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)                                  # 没有正在运行的循环：直接跑
        return None
    with ThreadPoolExecutor(max_workers=1) as pool:        # 图节点：另起一线程跑完，等它结束
        pool.submit(asyncio.run, coro).result()
    return None
