"""分析流程。控制流在 flows/graph.py 的 StateGraph 里（BLUEPRINT 19.5）；本文件保留
流程契约、Halt 降级与入口 analyze()，并负责运行前后的装配与收尾。

三条规则（图里的条件边逐条复刻）：
    1. 代码检查先于模型评审：审计不通过的方案不会花评审的 token。
    2. 返工从出问题的那一段重新开始，并重跑它之后的每一段（下游产物不能建立在已作废的上游之上）。
    3. 任何被改动过的方案，必须重新通过审计，才能进入评审或交付。
"""
from __future__ import annotations

from typing import Any, Literal

from ..domain.types import Contract, Outcome
from ..kernel.runtime import Halt, Malformed, Role, Run, run_role
from . import stages
from .graph import build_graph, initial_state


class FlowConfig(Contract):
    """流程的开关与上限。来自配置文件、环境变量、场景文件或网页表单，所以是一个契约：写错键名会报错。"""

    debate_rounds: int = 1
    audit: Literal["on", "shadow"] = "on"     # shadow：照常审计并记录，但不打回（消融实验用）
    judges: bool = True
    audit_rounds: int = 3                     # 审计最多打回几次
    judge_rounds: int = 2                     # 评审最多几轮（即最多返工 judge_rounds - 1 次）
    quorum: int = 2
    parallel: bool = True                     # 分析师并行（--serial 关掉）；两种模式的日志相同（不变量 19）
    channels: tuple[Literal["fundamentals", "disclosures"], ...] = ()   # 打开的证据通道；空 = 行为同 2.0


async def call(run: Run, role: Role) -> Any:
    """调用一个角色。输出两次都不合契约时停止运行（评审除外，见图的 judges 节点）。"""
    try:
        return await run_role(run, role)
    except Malformed as e:
        raise Halt("malformed_output", str(e)) from e


# ---- 入口 ----

async def drive(run: Run, request: dict[str, Any], deps: Any, cfg: FlowConfig | None = None, *,
                checkpointer: Any = None, thread_id: str | None = None,
                resume: bool = False) -> Outcome:
    """analyze 的可续跑形式：真实运行由 app 层带着检查点与线程 ID 调它（BLUEPRINT 19.5 第 6 条）。

    checkpointer 与 thread_id 一起给出时，图在检查点处留档；resume=True 表示输入为 None、
    从既有检查点继续（续跑前的状态由 app/resume.py 从日志重建）。
    """
    cfg = cfg or FlowConfig()
    holder: dict[str, Any] = {}                # 结果从这里取回：ainvoke 会浅拷贝 configurable，但不会拷贝这个字典本身
    config: dict[str, Any] = {"configurable": {"run": run, "request": request, "deps": deps,
                                               "holder": holder}}
    if thread_id is not None:
        config["configurable"]["thread_id"] = thread_id
    try:
        graph = build_graph(stages, cfg, deps, checkpointer=checkpointer)
        await graph.ainvoke(None if resume else initial_state(), config)
        result = holder["result"]
        outcome = Outcome(status="delivered", reason="ok",
                          facts_and_rules="verified" if result["verified"] else "unverified",
                          judgment=result["judgment"])
    except Halt as halt:
        outcome = _degrade(run, halt)
    run.board.put("run/outcome", outcome, by="flow")
    run.board.put("run/usage", run.budget.table(), by="flow")
    stages.report(run, deps)                            # 报告只读黑板，不计算
    run.journal.append("run.finished", actor="flow", data={"status": outcome.status,
                                                           "reason": outcome.reason})
    return outcome


async def analyze(run: Run, request: dict[str, Any], deps: Any, cfg: FlowConfig | None = None) -> Outcome:
    """deps 是代码阶段需要的外部依赖（行情、规则、指标声明、时钟），由 app 层装配后传入。

    入口参数保持 Any：参照测试 test_flow.py 以 None 传入（它用替身阶段，从不触到真实依赖），
    而参照测试不可改动。图从本模块引用的 stages 对象构建，所以测试替换 flow.stages 即替换全部代码阶段。
    """
    return await drive(run, request, deps, cfg)


_RESOURCE = ("budget_exhausted", "time_exhausted", "calls_exhausted", "model_unavailable")


def _degrade(run: Run, halt: Halt) -> Outcome:
    """运行被叫停后交付什么。

    只有一种情况仍然交付方案：资源耗尽时，黑板上的当前方案恰好是已通过审计的那一版，
    并且评审还没有对它给出过任何结论。其余情况一律 REVIEW：不给评级，不给价位。
    """
    audited = stages.audited_current(run)
    if halt.reason in _RESOURCE and audited and not run.board.has("judges/status"):
        return Outcome(status="delivered", reason=halt.reason, facts_and_rules="verified",
                       judgment=f"not reviewed: {halt.reason}")
    return Outcome(status="review", reason=halt.reason,
                   facts_and_rules="verified" if audited else "unverified",
                   judgment="contested" if halt.reason == "contested" else "not reviewed")
