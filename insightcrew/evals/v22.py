"""版本 2.2 的评测格子（BLUEPRINT 19.16、19.18 WP38）。

格子都是真实模型的运行：ACME（合成宇宙、逐个能力）与 AAPL、0700.HK（真实行情），
风险开/关、工具开/关、并行对串行、一次带组合、一次带课程。WP38 在 owner 给了
花费上限之后才真正跑；本模块只定义格子与汇总，不花钱。

接线（WP38 收尾时补）：evals/run.py 注册 v22；cli 的 eval 命令把 open_run/analyze
传进来。跑完的日志归档到 evals/archive/，全部必须重放一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..flows.analyze import FlowConfig


@dataclass(frozen=True, slots=True)
class Cell:
    """一个评测格子：一份请求 + 流程配置（+ 可选组合/记忆）。"""

    name: str                       # 格子名，如 "acme_all_on"
    ticker: str
    as_of: str                      # "" = 用最后一根已收盘 K 线
    universe: str                   # "synthetic" | "real"
    flow: FlowConfig
    portfolio: str = ""             # 组合文件路径；空 = 没有
    about: str = ""                 # 一句话：这个格子量什么


def cells() -> tuple[Cell, ...]:
    """19.16 的格子表。as_of 固定住，重跑才可比。"""
    acme = ("ACME", "2026-09-30", "synthetic")
    real_us, real_hk = ("AAPL", "2026-09-30", "real"), ("0700.HK", "2026-09-30", "real")
    on = FlowConfig(channels=("fundamentals", "disclosures", "news", "macro", "sentiment"),
                    risk_rounds=1, tools=True, memory=True)
    return (
        Cell("acme_all_on", *acme, on, about="全部能力开：一次跑通五项能力与并行分析"),
        Cell("acme_all_off", *acme, FlowConfig(), about="全部关：与 2.1 行为一致的基线"),
        Cell("acme_risk_on", *acme, FlowConfig(risk_rounds=1), about="只开风险辩论"),
        Cell("acme_tools_on", *acme, FlowConfig(tools=True), about="只开分析师工具"),
        Cell("acme_portfolio", *acme, FlowConfig(), portfolio="data/portfolios/flat_usd.json",
             about="带组合文件：规则与报告的组合节"),
        Cell("aapl_risk_on", *real_us, FlowConfig(risk_rounds=1), about="真实美股行情 + 风险"),
        Cell("aapl_tools_on", *real_us, FlowConfig(tools=True), about="真实美股行情 + 工具"),
        Cell("hk_all_on", *real_hk, on, about="真实港股行情 + 全部能力"),
        Cell("wall_serial", *acme, FlowConfig(parallel=False), about="串行墙钟（与并行格子对比）"),
    )


def summary(results: list[tuple[str, str]]) -> str:
    """一行一格的汇总：格子名 + 结果（delivered/review + 原因）。"""
    return "\n".join(f"{name}: {line}" for name, line in results) or "（没有格子运行）"


def no_spend_reason() -> str:
    """为什么现在不跑：等 owner 的花费批准与上限（HANDOFF 问题 14/29）。"""
    return "v22 的格子都是真实模型运行：等 owner 醒后给出花费上限（问题 14）再跑。"
