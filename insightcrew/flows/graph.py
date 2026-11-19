"""LangGraph 图：把 analyze.py 的手写控制流搬进 StateGraph（BLUEPRINT 19.5）。

图只管路由；账本、事实、审计仍由代码阶段（stages）计算，模型调用仍在
kernel/runtime.py 的 run_role 里。图的状态只有路由计数与去向（决策 41）：
Run（黑板、日志、预算）与 deps 经 config["configurable"] 传递，绝不进状态，
因此一个检查点永远可以对着日志核对。

条件边复刻 analyze.py 原来的两个循环（pass_audit 与 scrutinise）：
打回到最早的责任人并重跑其后每一段、同样的问题连续两次停机、审计与评审
各自的轮数上限、法定人数、评审只能否决。路由决定由节点算出并写进
state["route"]（路由事件也由节点就地记日志），条件边的 router 只读 state，
纯函数，显式 path_map 列出它可能返回的每一个值。
"""
from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

# RunnableConfig 的类型别名定义在 langgraph 的依赖 langchain_core 里；节点只有用它注解 config
# 参数，langgraph 才会把 config（连同 configurable）注入进来。除此之外不用 langchain_core。
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..domain.types import Verdict
from ..kernel.runtime import Halt, Malformed, Role, Run, commit, run_role, run_role_deferred
from ..roles import (
    BEAR,
    BULL,
    DISCLOSURES,
    FUNDAMENTALS,
    JUDGES,
    MANAGER,
    NEWS,
    SENTIMENT,
    TECHNICAL,
    TECHNICAL_TOOLS,
    TRADER,
)
from . import stage_memory, stage_portfolio, stage_risk
from .tools import build_tools

# 审计发现的责任人顺序（原 PIPELINE 顺序 + v2.2 的两个分析师；debate 不是责任人）。
_ORDER = ("technical", "fundamentals", "disclosures", "news", "sentiment", "manager", "trader")


class GraphState(TypedDict):
    """图的状态：只有路由计数与去向，此外什么都没有（决策 41）。
    每次运行以 initial_state() 全键起跑；节点返回部分更新，由 langgraph 合并。"""

    audit_round: int            # 本轮审计循环已打回几次（评审返工后清零，同原 pass_audit 的 attempt）
    judge_round: int            # 已完成的评审轮次（从 1 计）
    last_findings: list[str]    # 上一次审计的发现签名（"检查项|对象" 排序后），停机检测用
    route: str                  # 下一个要去的节点，由 audit/judges 节点算出
    halt: dict[str, str] | None # 预留：节点内捕获的停机原因（停机仍以异常方式直达 analyze）


async def _call(run: Run, role: Role) -> Any:
    """调用一个角色；两次不合契约即停机（评审除外，见 judges 节点）。与 analyze.call 同一条规则。"""
    try:
        return await run_role(run, role)
    except Malformed as e:
        raise Halt("malformed_output", str(e)) from e


def _ctx(config: RunnableConfig) -> dict[str, Any]:
    """Run、deps、request 都从 configurable 拿：它们不属于图状态。"""
    return config.get("configurable") or {}


def initial_state() -> GraphState:
    return {"audit_round": 0, "judge_round": 0, "last_findings": [], "route": "", "halt": None}


# ---- 路由表：显式列出每个 router 可能返回的每一个值 ----

JUDGE_MAP: tuple[str, ...] = ("manager", "trader", "outcome")
_ANALYSTS = ("technical", "fundamentals", "disclosures", "news", "sentiment")   # 打回时可能作为起点


def audit_map(cfg: Any, owners: tuple[str, ...], *, parallel: bool = False) -> Any:
    """audit 条件边的 path_map：在场责任人 + judges/outcome。

    串行模式责任人映射到同名节点；并行模式分析师责任人映射到扇出节点 analysts，
    路由值形如 "analysts:<owner>"（扇出从该分析师起，即 run_from 的语义）。
    """
    later = "judges" if cfg.judges else "outcome"
    analyst_owners = tuple(o for o in owners if o in _ANALYSTS)
    if not parallel:
        return [*owners, later]
    return {**{f"analysts:{owner}": "analysts" for owner in analyst_owners},
            "manager": "manager", "trader": "trader", later: later}


def _after_audit(state: GraphState) -> str:
    return state["route"]


def _after_judges(state: GraphState) -> str:
    return state["route"]


def build_graph(stages: Any, cfg: Any, deps: Any = None, *,
                checkpointer: Any = None) -> Any:
    """按流程配置建图。节点从不 import flows.stages：它们用 analyze 传进来的 stages 对象，
    所以测试可以整体替换（test_flow.py 的做法）。checkpointer 给出时图在检查点留档
    （真实运行用 SQLite，测试与续跑用内存，BLUEPRINT 19.5 第 6 条）。返回编译好的图。"""
    graph = StateGraph(GraphState)

    async def intake(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        ctx = _ctx(config)
        run = ctx["run"]
        d = ctx.get("deps", deps)
        stages.intake(run, ctx["request"], d)
        if cfg.tools and d is not None:
            # 工具在分析日绑定（19.8 第 5 条）：必须在 intake 写下 request/scope 之后建
            run.tools = build_tools(run, d, d.tool_specs)
        if cfg.memory and d is not None:
            stage_memory.lessons(run, d)   # 19.13：准入后读课程（只看已知结果的）
        return {}

    async def ledger(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        ctx = _ctx(config)
        run = ctx["run"]
        d = ctx.get("deps", deps)
        stages.ledger(run, d)
        if d is not None and d.portfolio is not None:
            stage_portfolio.portfolio(run, d)   # 19.9：账本之后；没有组合文件 = 能力关，一个键都不写
        return {}

    async def evidence(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        ctx = _ctx(config)
        stages.evidence(ctx["run"], ctx.get("deps", deps), cfg.channels)
        return {}

    def analyst(role: Role, name: str | None = None,
                ready: Callable[[Run], bool] | None = None) -> Callable[..., Awaitable[dict[str, Any]]]:
        """串行模式的分析师节点：通道分析师只有在通道可用时才发生模型调用（原 _fundamentals 的门）。"""
        async def node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
            ctx = _ctx(config)
            run = ctx["run"]
            if ready is None or ready(run):
                start = run.clock()
                await _call(run, role)
                _record_wall(ctx, name or role.name, start, run.clock())
            return {}
        node.__name__ = name or role.name
        return node

    def _record_wall(ctx: dict[str, Any], name: str, start: float, end: float) -> None:
        """每个分析师节点记录起止（19.5 第 5 条）。只暂存在 holder 里；
        持久化方式（黑板键 run/wall_ms，需 replay 忽略）待 HANDOFF 问题 16 定夺。"""
        ctx["holder"].setdefault("wall_ms", {})[name] = int((end - start) * 1000)

    def _deferred_analyst(role: Role, name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        """并行模式的分析师分支：算好不入账（run_role_deferred），Deferred 与耗时暂存，join 统一入账。"""
        async def node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
            ctx = _ctx(config)
            run = ctx["run"]
            if name != "technical" and not stages.channel_ready(run, name):
                return {}
            start = run.clock()
            deferred = await run_role_deferred(run, role)
            ctx["holder"].setdefault("deferred", {})[name] = deferred
            _record_wall(ctx, name, start, run.clock())
            return {}
        node.__name__ = name
        return node

    def _rerun_start(route: str) -> int:
        """本次扇出从哪个分析师开始：首轮从 0；打回时从最早责任人起（run_from 的语义）。"""
        if route.startswith("analysts:"):
            name = route.split(":", 1)[1]
            return [n for n, _ in analysts_group].index(name)
        return 0

    async def analysts(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        """并行模式的扇出前置节点：本次要跑的分析师先按固定顺序过闸门，任何一支分支开始之前。"""
        ctx = _ctx(config)
        run = ctx["run"]
        for name, role in analysts_group[_rerun_start(state["route"]):]:
            if name != "technical" and not stages.channel_ready(run, name):
                continue
            # step 编号与串行一致：该角色已入账的次数 + 1（预算事件里的步骤号不因并行而变）
            used = sum(1 for e in run.journal.events
                       if e.type in ("model.called", "model.failed") and e.actor == role.name)
            run.admit(role, f"{role.name}#{used + 1}")
        return {}

    def fan_out(state: GraphState) -> list[str]:
        """扇出路由：从起点开始的在场分析师各自成为一个并行分支（path_map 即全名单）。"""
        return [name for name, _ in analysts_group[_rerun_start(state["route"]):]]

    async def join(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        """汇聚点。并行模式：按固定顺序入账（technical、fundamentals、disclosures、…），
        谁先算完不打紧，日志与串行相同（决策 42、不变量 19）。两种模式都把工具事实收账
        （19.11：tools/facts 按调用顺序，tools 署名）。"""
        ctx = _ctx(config)
        run = ctx["run"]
        if cfg.parallel:
            pending: dict[str, Any] = ctx["holder"].get("deferred", {})
            for name, _ in analysts_group:
                deferred = pending.pop(name, None)
                if deferred is None:
                    continue
                try:
                    commit(run, deferred)
                except Malformed as e:
                    raise Halt("malformed_output", str(e)) from e
        facts = getattr(run.tools, "facts", None) if cfg.tools else None
        if facts:
            run.board.put("tools/facts", list(facts), by="tools")
        return {}

    async def debate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        run = _ctx(config)["run"]
        for _ in range(cfg.debate_rounds):
            await _call(run, BULL)
            await _call(run, BEAR)
        return {}

    async def manager(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        await _call(_ctx(config)["run"], MANAGER)
        return {}

    async def trader(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        await _call(_ctx(config)["run"], TRADER)
        return {}

    async def audit(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        """原 pass_audit 的循环体：审计、记路由事件、决定去向。停机以 Halt 直达 analyze。"""
        ctx = _ctx(config)
        findings = stages.audit(ctx["run"], ctx.get("deps", deps))
        if not findings:
            return {"route": "judges" if cfg.judges else "outcome"}
        if cfg.audit == "shadow":
            # 照常记录但不打回。评审返工后的再审计仍不通过，按原 scrutinise 的规则停机。
            if state["judge_round"] > 0:
                raise Halt("audit_failed", "评审返工后的方案未通过审计")
            return {"route": "judges" if cfg.judges else "outcome"}
        signature = sorted(f"{f.check}|{f.subject}" for f in findings)
        if signature == state["last_findings"]:
            raise Halt("stagnation", "同样的问题连续两次未被修正")
        if state["audit_round"] >= cfg.audit_rounds:
            raise Halt("audit_failed", f"打回 {cfg.audit_rounds} 次后仍未通过")
        earliest = min((f.owner for f in findings), key=_ORDER.index)
        ctx["run"].journal.append("route", actor="flow",
                                  data={"to": earliest, "why": f"审计发现 {len(findings)} 个问题"})
        # 并行模式下打回到分析师组：路由值带上起点（从最早责任人起），扇出据此截取名单
        target = f"analysts:{earliest}" if cfg.parallel and earliest in _ANALYSTS else earliest
        return {"audit_round": state["audit_round"] + 1, "last_findings": signature, "route": target}

    async def judges(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        """原 scrutinise 的一轮：按序询问、到法定人数即止、只能否决。"""
        run = _ctx(config)["run"]
        round_no = state["judge_round"] + 1
        verdicts: list[Verdict] = []
        for judge in JUDGES:
            try:
                verdicts.append(await run_role(run, judge))
            except Malformed:
                bad = Verdict(accept=False, target="none", critique="评审输出不合契约，按反对处理", cites=())
                run.board.put(judge.writes, bad, by="flow")
                verdicts.append(bad)
            accepts = sum(v.accept for v in verdicts)
            if accepts >= cfg.quorum or len(verdicts) - accepts > len(JUDGES) - cfg.quorum:
                break                                   # 结果已定，不再问剩下的评审

        dissents = [v for v in verdicts if not v.accept]
        run.board.put("judges/on_thesis", [v for v in dissents if v.target == "thesis"], by="flow")
        run.board.put("judges/on_levels", [v for v in dissents if v.target != "thesis"], by="flow")
        accepts = sum(v.accept for v in verdicts)
        if accepts >= cfg.quorum:
            label = f"accepted {accepts}/{len(JUDGES)}"
            run.board.put("judges/status", label, by="flow")
            return {"judge_round": round_no, "route": "outcome"}
        run.board.put("judges/status", "contested", by="flow")
        if round_no == cfg.judge_rounds:
            raise Halt("contested", "评审多数反对，返工次数已用尽")

        to_thesis = any(v.target == "thesis" for v in dissents)
        target = "manager" if to_thesis else "trader"
        run.journal.append("route", actor="flow",
                           data={"to": target, "why": "评审否决论点" if to_thesis else "评审否决价位或仓位"})
        # 返工后是一轮新的审计循环：计数与签名清零（原 scrutinise 里重新调用 pass_audit 的语义）。
        return {"judge_round": round_no, "audit_round": 0, "last_findings": [], "route": target}

    async def outcome(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        """把结果交给 analyze 的收尾：verified 与 judgment 从黑板读（等价于原 pass_audit 的返回值）。"""
        ctx = _ctx(config)
        run: Run = ctx["run"]
        verified = stages.audited_current(run)
        judgment = (run.board.get("judges/status") if run.board.has("judges/status")
                    else "not reviewed: judges disabled")
        ctx["holder"]["result"] = {"verified": verified, "judgment": judgment}
        return {}

    # ---- 节点与边：关掉的能力不加节点（19.5 第 1 条） ----

    graph.add_node("intake", intake)
    graph.add_node("ledger", ledger)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "ledger")

    if cfg.channels:
        graph.add_node("evidence", evidence)
        graph.add_edge("ledger", "evidence")
        head: str = "evidence"
    else:
        head = "ledger"

    # 在场的分析师（固定顺序，join 的入账顺序即此顺序）；audit 的责任人 = 分析师 + manager + trader
    technical = TECHNICAL
    if cfg.tools:          # 19.11：开工具时用 TECHNICAL 的工具形态，上限来自 [tools] 设置
        technical = dataclasses.replace(
            TECHNICAL_TOOLS, max_tool_calls=deps.tools_max_calls if deps is not None else 6)
    analysts_group: list[tuple[str, Role]] = [("technical", technical)]
    for channel, role in (("fundamentals", FUNDAMENTALS), ("disclosures", DISCLOSURES),
                          ("news", NEWS), ("sentiment", SENTIMENT)):
        if channel in cfg.channels:
            analysts_group.append((channel, role))
    owners = (*[name for name, _ in analysts_group], "manager", "trader")

    graph.add_node("join", join)
    graph.add_node("debate", debate)
    graph.add_node("manager", manager)
    graph.add_node("trader", trader)
    if cfg.risk_rounds > 0:
        async def risk_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
            ctx = _ctx(config)
            if ctx.get("deps", deps) is None:      # 替身阶段（tests/fake_stages）没有真依赖
                return {}
            stage_risk.risk(ctx["run"], ctx["deps"], cfg)
            return {}
        graph.add_node("risk", risk_node)
    graph.add_node("audit", audit)

    if cfg.parallel:
        # 并行：head -> analysts（整组先过闸门）-> 扇出到各分析师 -> join（固定顺序入账）-> debate
        graph.add_node("analysts", analysts)
        graph.add_edge(head, "analysts")
        graph.add_conditional_edges("analysts", fan_out, [name for name, _ in analysts_group])
        for name, role in analysts_group:
            graph.add_node(name, _deferred_analyst(role, name))
            graph.add_edge(name, "join")
    else:
        # 串行：head -> technical -> (通道分析师) -> join，通道不可用的分析师不发生模型调用
        prev = head
        for name, role in analysts_group:
            graph.add_node(name, analyst(role, name=name,
                                         ready=None if name == "technical" else
                                         (lambda run, c=name: stages.channel_ready(run, c))))
            graph.add_edge(prev, name)
            prev = name
        graph.add_edge(prev, "join")
    graph.add_edge("join", "debate")
    graph.add_edge("debate", "manager")
    graph.add_edge("manager", "trader")
    graph.add_edge("trader", "risk" if cfg.risk_rounds > 0 else "audit")
    if cfg.risk_rounds > 0:
        graph.add_edge("risk", "audit")

    audit_targets: Any = audit_map(cfg, owners, parallel=cfg.parallel)
    graph.add_conditional_edges("audit", _after_audit, audit_targets)
    if cfg.judges:
        graph.add_node("judges", judges)
        graph.add_conditional_edges("judges", _after_judges, list(JUDGE_MAP))
    graph.add_node("outcome", outcome)
    graph.add_edge("outcome", END)
    return graph.compile(checkpointer=checkpointer)
