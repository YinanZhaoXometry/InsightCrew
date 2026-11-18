"""分析流程。整个系统的控制流都在这一个文件里，从上往下读就是流程图。

三条规则：
    1. 代码检查先于模型评审：审计不通过的方案不会花评审的 token。
    2. 返工从出问题的那一段重新开始，并重跑它之后的每一段（下游产物不能建立在已作废的上游之上）。
    3. 任何被改动过的方案，必须重新通过审计，才能进入评审或交付。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from ..domain.types import Contract, Finding, Outcome, Verdict
from ..kernel.runtime import Halt, Malformed, Role, Run, run_role
from ..roles import BEAR, BULL, JUDGES, MANAGER, TECHNICAL, TRADER
from . import stages


class FlowConfig(Contract):
    """流程的开关与上限。来自配置文件、环境变量、场景文件或网页表单，所以是一个契约：写错键名会报错。"""

    debate_rounds: int = 1
    audit: Literal["on", "shadow"] = "on"     # shadow：照常审计并记录，但不打回（消融实验用）
    judges: bool = True
    audit_rounds: int = 3                     # 审计最多打回几次
    judge_rounds: int = 2                     # 评审最多几轮（即最多返工 judge_rounds - 1 次）
    quorum: int = 2


async def call(run: Run, role: Role) -> Any:
    """调用一个角色。输出两次都不合契约时停止运行（评审除外，见 scrutinise）。"""
    try:
        return await run_role(run, role)
    except Malformed as e:
        raise Halt("malformed_output", str(e)) from e


# ---- 流水线：每一段是一个函数，顺序即依赖关系 ----

async def _technical(run: Run, cfg: FlowConfig) -> None:
    await call(run, TECHNICAL)


async def _debate(run: Run, cfg: FlowConfig) -> None:
    for _ in range(cfg.debate_rounds):
        await call(run, BULL)
        await call(run, BEAR)


async def _manager(run: Run, cfg: FlowConfig) -> None:
    await call(run, MANAGER)


async def _trader(run: Run, cfg: FlowConfig) -> None:
    await call(run, TRADER)


PIPELINE: tuple[tuple[str, Callable[[Run, FlowConfig], Awaitable[None]]], ...] = (
    ("technical", _technical), ("debate", _debate), ("manager", _manager), ("trader", _trader))
_ORDER = [name for name, _ in PIPELINE]


async def run_from(run: Run, cfg: FlowConfig, start: str, why: str = "") -> None:
    """从 start 这一段开始，依次重跑它和它之后的每一段。"""
    if why:
        run.journal.append("route", actor="flow", data={"to": start, "why": why})
    for _, stage in PIPELINE[_ORDER.index(start):]:
        await stage(run, cfg)


# ---- 第一道关：代码审计（L2 数字，L3 规则）----

async def pass_audit(run: Run, cfg: FlowConfig, deps: Any) -> bool:
    """循环到审计通过为止。返回 True 表示当前方案已通过审计。"""
    previous = None
    for attempt in range(cfg.audit_rounds + 1):
        findings: list[Finding] = stages.audit(run, deps)
        if not findings:
            return True
        if cfg.audit == "shadow":
            return False
        signature = sorted((f.check, f.subject) for f in findings)
        if signature == previous:
            raise Halt("stagnation", "同样的问题连续两次未被修正")
        if attempt == cfg.audit_rounds:
            raise Halt("audit_failed", f"打回 {cfg.audit_rounds} 次后仍未通过")
        previous = signature
        earliest = min((f.owner for f in findings), key=_ORDER.index)
        await run_from(run, cfg, earliest, why=f"审计发现 {len(findings)} 个问题")
    raise AssertionError("unreachable")


# ---- 第二道关：模型评审（L4 判断）。评审只能否决，不能改数字 ----

async def scrutinise(run: Run, cfg: FlowConfig, deps: Any) -> str:
    """返回判断标签，例如 "accepted 2/3"。多数反对且返工用尽时停止运行。"""
    for round_no in range(1, cfg.judge_rounds + 1):
        verdicts: list[Verdict] = []
        accepts = 0
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
        if accepts >= cfg.quorum:
            label = f"accepted {accepts}/{len(JUDGES)}"
            run.board.put("judges/status", label, by="flow")
            return label
        run.board.put("judges/status", "contested", by="flow")
        if round_no == cfg.judge_rounds:
            raise Halt("contested", "评审多数反对，返工次数已用尽")

        to_thesis = any(v.target == "thesis" for v in dissents)
        await run_from(run, cfg, "manager" if to_thesis else "trader",
                 why="评审否决论点" if to_thesis else "评审否决价位或仓位")
        if not await pass_audit(run, cfg, deps):
            raise Halt("audit_failed", "评审返工后的方案未通过审计")
    raise AssertionError("unreachable")


# ---- 入口 ----

async def analyze(run: Run, request: dict[str, Any], deps: Any, cfg: FlowConfig | None = None) -> Outcome:
    """deps 是代码阶段需要的外部依赖（行情、规则、指标声明、时钟），由 app 层装配后传入。"""
    cfg = cfg or FlowConfig()
    try:
        stages.intake(run, request, deps)               # 范围闸门：不在范围内直接 Halt
        stages.ledger(run, deps)                        # 事实账本：之后任何数字都必须来自这里
        await run_from(run, cfg, "technical")
        verified = await pass_audit(run, cfg, deps)
        judgment = await scrutinise(run, cfg, deps) if cfg.judges else "not reviewed: judges disabled"
        outcome = Outcome(status="delivered", reason="ok",
                          facts_and_rules="verified" if verified else "unverified", judgment=judgment)
    except Halt as halt:
        outcome = _degrade(run, halt)
    run.board.put("run/outcome", outcome, by="flow")
    run.board.put("run/usage", run.budget.table(), by="flow")
    stages.report(run, deps)                            # 报告只读黑板，不计算
    run.journal.append("run.finished", actor="flow", data={"status": outcome.status,
                                                           "reason": outcome.reason})
    return outcome


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
