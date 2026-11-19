"""记忆阶段（BLUEPRINT 19.13）。lessons 在准入后运行（memory 打开才有），
record 在运行结束、结果为 delivered 之后由 analyze 的收尾调用。

lessons 只看分析日之前已知结果的教训（不变量 20：绝不学到还没发生的结果）。
重放时决策日志端口是按日志供给的替身（决策 44）：journaled_lessons 原样奉还，
这里照抄上板，不读任何外部文件；场景的内联记录是字典行，先按契约校验再挑选。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.memory import (
    DecisionRecord,
    LessonEntry,
    OutcomeRecord,
    select_lessons,
)
from ..domain.types import Proposal, Scope, Thesis
from ..flows.deps import DecisionLogPort, Deps
from ..kernel.runtime import Run


def lessons(run: Run, deps: Deps) -> None:
    """把 select_lessons 挑出的教训写上黑板（memory/lessons 与 memory/status）。

    端口带 journaled_lessons（重放替身）时按日志原样放回（决策 44）；
    否则从端口的记录里挑：场景的内联行是字典，先校验成契约再交给 select_lessons。
    19.8 第 4 条的显式缺席：一条也没有时写空表与 none_known 状态，并给一句原因。
    """
    log = deps.decision_log
    journaled = getattr(log, "journaled_lessons", None)
    if journaled is not None:
        run.board.put("memory/lessons", list(journaled), by="memory")
        status = getattr(log, "journaled_status", None) or {"state": "none_known", "reason": ""}
        run.board.put("memory/status", dict(status), by="memory")
        return

    scope = run.board.get("request/scope", Scope)
    if log is None:
        entries: tuple[LessonEntry, ...] = ()
        reason = "决策日志未接线"
    else:
        entries = select_lessons(_records(log), scope.ticker, date.fromisoformat(scope.as_of),
                                 same=deps.memory_same_ticker, other=deps.memory_other_ticker)
        reason = ""
    run.board.put("memory/lessons", list(entries), by="memory")
    run.board.put("memory/status",
                  {"state": "available" if entries else "none_known",
                   "reason": reason or ("" if entries else "决策日志里没有分析日之前已写教训的结算结果")},
                  by="memory")


def record(run: Run, deps: Deps) -> None:
    """交付的运行追加一条 DecisionRecord：评级取自主管的论点，方案字段取自交易员提案。

    重放与场景的日志端口写入是空操作（决策 44），所以这里不需要知道自己是不是重放；
    同一 (as_of, ticker) 的第二条决策由日志自己忽略（19.13 的幂等规则）。
    """
    log: Any = deps.decision_log     # 端口协议只声明读；写入由真实日志实现，替身是空写
    if log is None:
        return
    scope = run.board.get("request/scope", Scope)
    thesis = run.board.get("manager/thesis", Thesis)
    proposal = run.board.get("trader/proposal", Proposal)
    log.append(DecisionRecord(
        kind="decision", run_id=run.id, ticker=scope.ticker, as_of=scope.as_of,
        rating=thesis.rating, action=proposal.action, entry=proposal.entry,
        stop=proposal.stop, target=proposal.target, size_pct=proposal.size_pct,
        horizon_days=proposal.horizon_days))


def _records(log: DecisionLogPort) -> list[DecisionRecord | OutcomeRecord]:
    """端口的记录行。真实日志给契约实例；场景内联（_ScenarioLog）给字典，这里校验。"""
    rows: list[Any] = list(log.records())
    out: list[DecisionRecord | OutcomeRecord] = []
    for row in rows:
        if isinstance(row, (DecisionRecord, OutcomeRecord)):
            out.append(row)
            continue
        contract = OutcomeRecord if row.get("kind") == "outcome" else DecisionRecord
        out.append(contract.model_validate(row))
    return out
