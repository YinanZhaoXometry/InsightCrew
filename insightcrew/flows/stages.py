"""代码阶段：把纯领域函数接到黑板上。

每个阶段做三件事：从黑板读输入、调用 domain 里的纯函数、把结果写回黑板。
阶段本身不含业务计算，也不调用模型。数据类的错误在这里就地转成有名字的 Halt，
让流程统一降级为 REVIEW（见 BLUEPRINT 8.2），绝不带着异常穿过运行。
"""
from __future__ import annotations

import json
from datetime import date

from ..domain import rules as rulebook
from ..domain import symbols
from ..domain.audit import audit as audit_numbers
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import InvalidTicker, NoData, SourceUnavailable, UnknownTicker
from ..domain.types import (
    Analysis,
    AuditStatus,
    Fact,
    Finding,
    Proposal,
    Scope,
    Thesis,
)
from ..kernel.runtime import Halt, Run
from .deps import Deps

_INTAKE_RULES = ("data_fresh", "min_history")


def intake(run: Run, request: dict, deps: Deps) -> None:
    """范围闸门。通过则写 request/scope；不在范围内则抛 Halt(原因)，此时不发生任何模型调用。"""
    raw = request.get("ticker", "")
    try:
        ticker = symbols.normalize(raw)
        _ = deps.market.instrument(ticker)             # 在准入时就确认代码在数据集里
    except InvalidTicker as e:
        raise Halt("invalid_ticker", str(e)) from e
    except UnknownTicker as e:
        raise Halt("unknown_ticker", str(e)) from e

    given = request.get("as_of", "")
    if given:
        as_of = date.fromisoformat(given)              # 无法解析的日期是调用方的错：命令行与表单都先校验
        if as_of > deps.today:
            raise Halt("future_date", f"分析日 {as_of} 晚于今天")
    else:
        as_of = None
    bars = _history(deps, ticker, as_of or deps.today)
    if as_of is None:
        as_of = bars[-1].date                          # 默认分析日：最后一根已收盘K线的日期

    _intake_rules(deps, last_bar=bars[-1].date, as_of=as_of, count=len(bars))

    scope = Scope(ticker=ticker, as_of=as_of.isoformat(), universe=deps.universe, requested=raw)
    run.board.put("request/scope", scope, by="intake")
    run.journal.append("gate.checked", actor="intake", data={"gate": "intake", "findings": 0})


def _intake_rules(deps: Deps, *, last_bar: date, as_of: date, count: int) -> None:
    """范围的两条规则在准入时检查。失败各自成为停机原因：数据过旧、历史不足。"""
    specs = {spec.key: spec for spec in deps.rules}
    if "data_fresh" in specs:
        result = rulebook.data_fresh(last_bar, as_of, specs["data_fresh"])
        if result.status == "fail":
            raise Halt("stale_data", result.message)
    if "min_history" in specs:
        result = rulebook.min_history(count, specs["min_history"])
        if result.status == "fail":
            raise Halt("insufficient_history", result.message)


def ledger(run: Run, deps: Deps) -> None:
    """建立事实账本并用第二条路径复算。写 ledger/instrument、ledger/facts、ledger/card、rules/limits。"""
    scope = run.board.get("request/scope", Scope)
    as_of = date.fromisoformat(scope.as_of)
    instrument = _instrument(deps, scope.ticker)
    bars = _history(deps, scope.ticker, as_of)

    facts, card = build_ledger(instrument, bars, as_of, deps.indicators,
                               deps.market.dropped_rows(scope.ticker))
    recomputed = recompute_ledger(instrument, bars, as_of, deps.indicators)
    if dis := disagreements(facts, recomputed):
        raise Halt("ledger_inconsistent", "、".join(dis))

    run.board.put("ledger/instrument", instrument, by="ledger")
    run.board.put("ledger/facts", facts, by="ledger")
    run.board.put("ledger/card", card, by="ledger")
    run.board.put("rules/limits", [s for s in deps.rules if s.key not in _INTAKE_RULES], by="ledger")
    run.journal.append("gate.checked", actor="ledger", data={"gate": "ledger", "findings": 0})


def _instrument(deps: Deps, ticker: str):
    try:
        return deps.market.instrument(ticker)
    except UnknownTicker as e:
        raise Halt("unknown_ticker", str(e)) from e
    except SourceUnavailable as e:
        raise Halt("source_unavailable", str(e)) from e


def _history(deps: Deps, ticker: str, as_of: date):
    try:
        return deps.market.history(ticker, as_of)
    except NoData as e:
        raise Halt("no_data", str(e)) from e
    except SourceUnavailable as e:
        raise Halt("source_unavailable", str(e)) from e


def audit(run: Run, deps: Deps) -> list[Finding]:
    """L2 数字核对 + L3 规则检查。每次调用都重写 audit/* 各键，并记一条 gate.checked 事件。"""
    facts: list[Fact] = run.board.get("ledger/facts", list[Fact])
    analysis = run.board.get("technical/analysis", Analysis)
    thesis = run.board.get("manager/thesis", Thesis)
    proposal = run.board.get("trader/proposal", Proposal)

    values = {f.id: f.value for f in facts}
    rule_results = rulebook.evaluate(proposal, thesis, values, deps.rules)
    findings = audit_numbers(facts, analysis, thesis, proposal, rule_results)

    run.board.put("audit/rules", rule_results, by="audit")
    run.board.put("audit/findings", findings, by="audit")
    for owner in ("technical", "manager", "trader"):
        run.board.put(f"audit/{owner}", [f for f in findings if f.owner == owner], by="audit")
    status = AuditStatus(verdict="pass" if not findings else "revise",
                         proposal_seq=run.board.seq("trader/proposal"),
                         thesis_seq=run.board.seq("manager/thesis"),
                         analysis_seq=run.board.seq("technical/analysis"))
    run.board.put("audit/status", status, by="audit")
    run.journal.append("gate.checked", actor="audit", data={"gate": "audit", "findings": len(findings)})
    return findings


def audited_current(run: Run) -> bool:
    """黑板上的当前读数、论点、方案，是否就是审计通过的那一版（比较 audit/status 里记录的序号）。"""
    board = run.board
    if not board.has("audit/status"):
        return False
    status = board.get("audit/status")
    return (status["verdict"] == "pass" and status["proposal_seq"] == board.seq("trader/proposal")
            and status["thesis_seq"] == board.seq("manager/thesis")
            and status["analysis_seq"] == board.seq("technical/analysis"))


def report(run: Run, deps: Deps) -> None:
    """把黑板渲染成报告并写入运行目录。只读黑板，不做任何计算。"""
    if deps.out_dir is None or deps.render is None:
        return
    snapshot = run.board.snapshot()
    (deps.out_dir / "board.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (deps.out_dir / "report.md").write_text(deps.render(snapshot), encoding="utf-8")
