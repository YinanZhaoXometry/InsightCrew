"""三方风险辩论（BLUEPRINT 19.10）。WP31 留桩：签名即最终签名，WP32 填正文。

risk 节点只在 FlowConfig.risk_rounds > 0 时加入图，在 trader 之后、audit 之前。
每轮依次调用 risk.aggressive、risk.conservative、risk.neutral，然后交易员再改一次方案；
审计打回到 trader 时，risk 在它之后重跑（图的线性边 trader -> risk -> audit 已保证）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..flows.deps import Deps
from ..kernel.runtime import Run

if TYPE_CHECKING:
    from .analyze import FlowConfig  # 仅类型引用：analyze 已引入 graph，避免加载环


def risk(run: Run, deps: Deps, cfg: FlowConfig) -> None:
    """WP32 填写：按轮数调用三个风险角色并让交易员修订一次。桩阶段不写任何键。"""
    return None
