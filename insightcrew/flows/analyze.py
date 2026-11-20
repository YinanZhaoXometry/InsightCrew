"""分析流程。控制流在 flows/graph.py 的 StateGraph 里（BLUEPRINT 19.5）；本文件保留
流程契约、Halt 降级与入口 analyze()，并负责运行前后的装配与收尾。

三条规则（图里的条件边逐条复刻）：
    1. 代码检查先于模型评审：审计不通过的方案不会花评审的 token。
    2. 返工从出问题的那一段重新开始，并重跑它之后的每一段（下游产物不能建立在已作废的上游之上）。
    3. 任何被改动过的方案，必须重新通过审计，才能进入评审或交付。

版本 2.3（BLUEPRINT 20.7）：本模块的 call 成为流程层唯一的一份角色调用封装，graph 与
stage_risk 都从这里引入（第 6 条，import 环因此拆开：drive 在函数体内才引入 graph）；
drive 的续跑入口在 intake 不重跑时重建工具（第 3 条）；Halt 降级前先把并行兄弟分支
已算完的结果入账（第 4 条）；墙钟不再上板，改由侧车 trace 记录（第 5 条）。
"""
from __future__ import annotations

import contextlib
from typing import Any, Literal

from ..domain.types import Contract, Fact, Outcome
from ..kernel.runtime import Halt, Malformed, Role, Run, commit, run_role
from . import stages
from .tools import ToolBox, build_tools

# 并行分析师的固定顺序（BLUEPRINT 19.5 第 4 条）：扇出、入账与停机补账都按它来。
ANALYST_ORDER: tuple[str, ...] = ("technical", "fundamentals", "disclosures", "news", "sentiment")


class FlowConfig(Contract):
    """流程的开关与上限。来自配置文件、环境变量、场景文件或网页表单，所以是一个契约：写错键名会报错。"""

    debate_rounds: int = 1
    audit: Literal["on", "shadow"] = "on"     # shadow：照常审计并记录，但不打回（消融实验用）
    judges: bool = True
    audit_rounds: int = 3                     # 审计最多打回几次
    judge_rounds: int = 2                     # 评审最多几轮（即最多返工 judge_rounds - 1 次）
    quorum: int = 2
    parallel: bool = True                     # 分析师并行（--serial 关掉）；两种模式的日志相同（不变量 19）
    channels: tuple[Literal["fundamentals", "disclosures", "news", "sentiment", "macro"], ...] = ()
    # ^ 打开的证据通道；空 = 行为同 2.0。macro 需要点名 news（prepare 处拒绝只开 macro 的组合）。
    risk_rounds: int = 0                      # 三方风险辩论的轮数；0 = 不加 risk 节点（19.10）
    tools: bool = False                       # 技术分析师可用 indicator 工具（19.11）
    memory: bool = False                      # 记忆：开运行读课程、交付后记决策（19.13）


async def call(run: Run, role: Role) -> Any:
    """调用一个角色。输出两次都不合契约时停止运行（评审除外，见图的 judges 节点）。

    流程层唯一的一份（20.7 第 6 条）：graph 与 stage_risk 都引入它，不再各持一份。
    """
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
    from .graph import build_graph, initial_state  # 函数体内引入：graph 要引入本模块的 call（20.7 第 6 条）

    cfg = cfg or FlowConfig()
    holder: dict[str, Any] = {}                # 结果从这里取回：ainvoke 会浅拷贝 configurable，但不会拷贝这个字典本身
    config: dict[str, Any] = {"configurable": {"run": run, "request": request, "deps": deps,
                                               "holder": holder}}
    if thread_id is not None:
        config["configurable"]["thread_id"] = thread_id
    if resume and cfg.tools and run.board.has("request/scope"):
        _rebuild_tools(run, deps)
    try:
        graph = build_graph(stages, cfg, deps, checkpointer=checkpointer)
        await graph.ainvoke(None if resume else initial_state(), config)
        result = holder["result"]
        outcome = Outcome(status="delivered", reason="ok",
                          facts_and_rules="verified" if result["verified"] else "unverified",
                          judgment=result["judgment"])
    except Halt as halt:
        _commit_siblings(run, holder)          # 20.7 第 4 条：先入账兄弟分支已算完的结果，再降级
        outcome = _degrade(run, halt)
    run.board.put("run/outcome", outcome, by="flow")
    run.board.put("run/usage", run.budget.table(), by="flow")
    if cfg.memory and outcome.status == "delivered":
        from . import stage_memory
        stage_memory.record(run, deps)                  # 19.13：交付的运行追加一条决策；重放端口是空写替身
    stages.report(run, deps)                            # 报告只读黑板，不计算
    run.journal.append("run.finished", actor="flow", data={"status": outcome.status,
                                                           "reason": outcome.reason})
    return outcome


def _rebuild_tools(run: Run, deps: Any) -> None:
    """续跑入口的工具重建（20.7 第 3 条，D3 流程这一半）：续跑不重跑 intake，工具在这里
    重建；板上已有的工具事实装回工具盒，重跑同一调用不重复收集、新事实接在旧事实之后。
    替身阶段（deps 为 None）没有真依赖，也没有工具可建。
    """
    if deps is None:
        return
    box: ToolBox = build_tools(run, deps, deps.tool_specs)
    if run.board.has("tools/facts"):
        box.seed(run.board.get("tools/facts", list[Fact]))
    run.tools = box


def _commit_siblings(run: Run, holder: dict[str, Any]) -> None:
    """并行分支里有停机时，把其余分支已算好、还没到 join 入账的结果按固定顺序补进日志与
    预算（20.7 第 4 条）：兄弟的花费不该随停机消失。不合契约的（Malformed）忽略：
    停机原因仍然主导，不合契约的输出本来也上不了板。
    """
    pending = holder.get("deferred", {})
    for name in ANALYST_ORDER:
        deferred = pending.pop(name, None)
        if deferred is None:
            continue
        with contextlib.suppress(Malformed):
            commit(run, deferred)


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
