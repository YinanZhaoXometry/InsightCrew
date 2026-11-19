"""代码阶段：把纯领域函数接到黑板上。

每个阶段做三件事：从黑板读输入、调用 domain 里的纯函数、把结果写回黑板。
阶段本身不含业务计算，也不调用模型。

三道闸门（准入、账本、审计）每次检查都留下一条 gate.checked 事件，通过与否都记：
findings 是问题的条数，detail 是每个问题的 {check, subject, message}。闸门拦下运行时，
先记这条事件再抛 Halt，所以"为什么停"的细节在日志里，而不只是一个原因代号。
数据类的错误在这里就地转成有名字的 Halt，由流程统一降级为 REVIEW（见 BLUEPRINT 8.2）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal

from ..domain import rules as rulebook
from ..domain import symbols
from ..domain.audit import audit as audit_numbers
from ..domain.fundamentals_ledger import build_fundamentals, recompute_fundamentals
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import (
    Bar,
    Instrument,
    InvalidTicker,
    NoData,
    NotCovered,
    SourceUnavailable,
    UnknownTicker,
)
from ..domain.types import (
    Analysis,
    AnalysisSeq,
    AuditStatus,
    ChannelStatus,
    DataCard,
    DisclosureAnalysis,
    DisclosureCard,
    Fact,
    Finding,
    Item,
    Proposal,
    Scope,
    Thesis,
)
from ..kernel.runtime import Halt, Run
from .deps import Deps

_INTAKE_RULES = ("data_fresh", "min_history")
_OWNERS = ("technical", "fundamentals", "disclosures", "manager", "trader")
_CHANNELS: tuple[Literal["fundamentals", "disclosures"], ...] = ("fundamentals", "disclosures")


def _gate(run: Run, gate: str, detail: list[dict[str, Any]]) -> None:
    run.journal.append("gate.checked", actor=gate,
                       data={"gate": gate, "findings": len(detail), "detail": detail})


def _refused(run: Run, gate: str, halt: Halt) -> None:
    """闸门拦下了运行：把原因与说明记进日志。调用方随后原样抛出这个 Halt。"""
    _gate(run, gate, [{"check": halt.reason, "subject": "request", "message": halt.detail}])


# ---- 准入 ----

def intake(run: Run, request: dict[str, Any], deps: Deps) -> None:
    """范围闸门。通过则写 request/scope；不在范围内则抛 Halt(原因)，此时不发生任何模型调用。"""
    try:
        scope = _scope(request, deps)
    except Halt as halt:
        _refused(run, "intake", halt)
        raise
    run.board.put("request/scope", scope, by="intake")
    _gate(run, "intake", [])


def _scope(request: dict[str, Any], deps: Deps) -> Scope:
    raw = str(request.get("ticker", ""))
    try:
        ticker = symbols.normalize(raw)
    except InvalidTicker as e:
        raise Halt("invalid_ticker", str(e)) from e
    _instrument(deps, ticker)                           # 在准入时就确认代码在数据集里

    given = request.get("as_of")
    if given:
        # 无法解析的日期不是一种停机原因：它是调用方的错，由 app 层在开运行之前拒绝（wiring.open_run）。
        as_of = date.fromisoformat(given)
        if as_of > deps.today:
            raise Halt("future_date", f"分析日 {as_of} 晚于今天 {deps.today}")
        bars = _history(deps, ticker, as_of)
    else:
        bars = _history(deps, ticker, deps.today)
        as_of = bars[-1].date                           # 默认分析日：最后一根已收盘 K 线的日期

    specs = {spec.key: spec for spec in deps.rules}
    if "data_fresh" in specs:
        fresh = rulebook.data_fresh(bars[-1].date, as_of, specs["data_fresh"])
        if fresh.status == "fail":
            raise Halt("stale_data", fresh.message)
    if "min_history" in specs:
        enough = rulebook.min_history(len(bars), specs["min_history"])
        if enough.status == "fail":
            raise Halt("insufficient_history", enough.message)
    return Scope(ticker=ticker, as_of=as_of.isoformat(), universe=deps.universe, requested=raw)


def _instrument(deps: Deps, ticker: str) -> Instrument:
    try:
        return deps.market.instrument(ticker)
    except UnknownTicker as e:
        raise Halt("unknown_ticker", str(e)) from e
    except SourceUnavailable as e:
        raise Halt("source_unavailable", str(e)) from e


def _history(deps: Deps, ticker: str, as_of: date) -> tuple[Bar, ...]:
    try:
        return deps.market.history(ticker, as_of)
    except NoData as e:
        raise Halt("no_data", str(e)) from e
    except SourceUnavailable as e:
        raise Halt("source_unavailable", str(e)) from e


# ---- 账本 ----

def ledger(run: Run, deps: Deps) -> None:
    """建立事实账本并用第二条路径复算。写 ledger/instrument、ledger/facts、ledger/card、rules/limits。"""
    scope = run.board.get("request/scope", Scope)
    as_of = date.fromisoformat(scope.as_of)
    try:
        instrument = _instrument(deps, scope.ticker)
        bars = _history(deps, scope.ticker, as_of)
    except Halt as halt:
        _refused(run, "ledger", halt)
        raise

    facts, card = build_ledger(instrument, bars, as_of, deps.indicators,
                               deps.market.dropped_rows(scope.ticker))
    differing = disagreements(facts, recompute_ledger(instrument, bars, as_of, deps.indicators))
    if differing:
        _gate(run, "ledger", [{"check": "ledger_inconsistent", "subject": fact_id,
                               "message": "两条计算路径给出的结果不一致"} for fact_id in differing])
        raise Halt("ledger_inconsistent", "、".join(differing))

    run.board.put("ledger/instrument", instrument, by="ledger")
    run.board.put("ledger/facts", facts, by="ledger")
    run.board.put("ledger/card", card, by="ledger")
    run.board.put("rules/limits", [s for s in deps.rules if s.key not in _INTAKE_RULES], by="ledger")
    _gate(run, "ledger", [])


# ---- 证据通道（BLUEPRINT 18.8）----


def evidence(run: Run, deps: Deps, channels: tuple[Literal["fundamentals", "disclosures"], ...]) -> None:
    """证据阶段。按 fundamentals、disclosures 的固定顺序读取每个被点名的通道，
    写它的键，最后记一条 gate.checked（gate = "evidence"）。

    通道读不到绝不停下运行（BLUEPRINT 决策 35）：不可用就写明原因，继续；
    唯一的例外是基本面两条计算路径的分歧——那是代码的 bug，停机核对。
    """
    scope = run.board.get("request/scope", Scope)
    as_of = date.fromisoformat(scope.as_of)
    instrument = run.board.get("ledger/instrument", Instrument)
    card = run.board.get("ledger/card", DataCard)
    ledger = {f.id: f for f in run.board.get("ledger/facts", list[Fact])}

    for channel in _CHANNELS:                       # 固定顺序，与 channels 里的书写顺序无关
        if channel not in channels:
            continue
        if channel == "fundamentals":
            close = ledger["close"].value if "close" in ledger else None
            close_date = date.fromisoformat(card.last_bar)
            _fundamentals(run, deps, scope.ticker, instrument, close, close_date, as_of)
        else:
            _disclosures(run, deps, scope.ticker, as_of)
    _gate(run, "evidence", [])


def _fundamentals(run: Run, deps: Deps, ticker: str, instrument: Instrument,
                  close: float | None, close_date: date, as_of: date) -> None:
    if deps.statements is None or not deps.fundamentals or close is None:
        _channel_off(run, "fundamentals", "尚未接入年报数据源")
        return
    try:
        ss = deps.statements.statements(ticker, as_of)
    except (NotCovered, SourceUnavailable) as e:
        _channel_off(run, "fundamentals", str(e))
        return
    if not ss.rows:
        run.board.put("fundamentals/status",
                      ChannelStatus(channel="fundamentals", state="empty",
                                    reason="数据源已回答，分析日之前没有任何已申报的年度报表"),
                      by="evidence")
        return
    facts, card = build_fundamentals(instrument, ss, close, close_date, as_of, deps.fundamentals)
    if not facts:                                   # 有申报但一条事实也算不出（例如缺申报概念）
        run.board.put("fundamentals/status",
                      ChannelStatus(channel="fundamentals", state="empty",
                                    reason="已申报的年度报表算不出任何基本面事实"),
                      by="evidence")
        return
    # 申报货币与标的货币不同时，价格类声明（pe_fy）不参与复算：路径 1 本就不产出它
    # （HANDOFF 问题 9 的答案），路径 2 也收到剔除后的声明，两条路径才能对上。
    specs = deps.fundamentals
    if ss.currency != instrument.currency:
        specs = tuple(s for s in specs if s.fn != "price_multiple")
    differing = disagreements(facts, recompute_fundamentals(ss, close, as_of, specs))
    if differing:
        _gate(run, "evidence", [{"check": "ledger_inconsistent", "subject": fact_id,
                                 "message": "两条基本面计算路径给出的结果不一致"}
                                for fact_id in differing])
        raise Halt("ledger_inconsistent", "、".join(f"fundamentals/{f}" for f in differing))
    run.board.put("fundamentals/facts", facts, by="evidence")
    run.board.put("fundamentals/card", card, by="evidence")
    run.board.put("fundamentals/status",
                  ChannelStatus(channel="fundamentals", state="available", reason=""), by="evidence")


def _disclosures(run: Run, deps: Deps, ticker: str, as_of: date) -> None:
    if deps.disclosures is None:
        _channel_off(run, "disclosures", "尚未接入公告数据源")
        return
    start = as_of - timedelta(days=deps.disclosure_window_days)
    try:
        ds = deps.disclosures.published(ticker, start, as_of)
    except (NotCovered, SourceUnavailable) as e:
        _channel_off(run, "disclosures", str(e))
        return
    ordered = sorted(ds.items, key=lambda p: (-p.published.toordinal(), p.ref))
    kept = ordered[:deps.disclosure_max_items]
    items = [Item(id=f"D{i}", published=p.published.isoformat(), source=ds.source,
                  kind=p.kind, title=p.title, ref=p.ref)
             for i, p in enumerate(kept, start=1)]
    run.board.put("disclosures/items", items, by="evidence")
    run.board.put("disclosures/card", DisclosureCard(
        source=ds.source, window_from=start.isoformat(), window_to=as_of.isoformat(),
        items=len(items), dropped=ds.dropped,
        truncated=max(0, len(ordered) - len(kept))), by="evidence")
    run.board.put("disclosures/status",
                  ChannelStatus(channel="disclosures", state="available" if items else "empty",
                                reason="" if items else
                                f"窗口 {start.isoformat()} 到 {as_of.isoformat()} 内没有公告"),
                  by="evidence")


def _channel_off(run: Run, channel: Literal["fundamentals", "disclosures"], reason: str) -> None:
    """通道不可用：只写状态与原因，此外一个键都不写（18.8）。"""
    run.board.put(f"{channel}/status",
                  ChannelStatus(channel=channel, state="unavailable", reason=reason), by="evidence")


def channel_ready(run: Run, channel: str) -> bool:
    """通道可用：状态键存在且 state 为 available。空、不可用或未打开都算没准备好。"""
    if not run.board.has(f"{channel}/status"):
        return False
    return run.board.get(f"{channel}/status", ChannelStatus).state == "available"


# ---- 审计 ----

def audit(run: Run, deps: Deps) -> list[Finding]:
    """L2 数字核对 + L3 规则检查。每次调用都重写 audit/* 各键，并记一条 gate.checked 事件。

    通道分析在板上才交给审计（18.8）：audit/fundamentals、audit/disclosures 和 audit/channels
    只在对应的键存在时写，所以通道关闭的运行里审计写出的键与版本 2.0 相同。
    """
    facts = run.board.get("ledger/facts", list[Fact])
    analysis = run.board.get("technical/analysis", Analysis)
    thesis = run.board.get("manager/thesis", Thesis)
    proposal = run.board.get("trader/proposal", Proposal)

    fundamentals = None
    if run.board.has("fundamentals/analysis"):
        fundamentals = (run.board.get("fundamentals/facts", list[Fact]),
                        run.board.get("fundamentals/analysis", Analysis))
    disclosures = None
    if run.board.has("disclosures/analysis"):
        disclosures = (run.board.get("disclosures/items", list[Item]),
                       run.board.get("disclosures/analysis", DisclosureAnalysis))

    rule_results = rulebook.evaluate(proposal, thesis, {f.id: f.value for f in facts}, deps.rules)
    findings = audit_numbers(facts, analysis, thesis, proposal, rule_results,
                             fundamentals=fundamentals, disclosures=disclosures)

    run.board.put("audit/rules", rule_results, by="audit")
    run.board.put("audit/findings", findings, by="audit")
    for owner in _OWNERS:
        if owner in _CHANNELS and not run.board.has(f"{owner}/analysis"):
            continue
        run.board.put(f"audit/{owner}", [f for f in findings if f.owner == owner], by="audit")
    if fundamentals is not None or disclosures is not None:
        run.board.put("audit/channels",
                      [AnalysisSeq(key=f"{c}/analysis", seq=run.board.seq(f"{c}/analysis"))
                       for c in _CHANNELS if run.board.has(f"{c}/analysis")], by="audit")
    run.board.put("audit/status", AuditStatus(
        verdict="revise" if findings else "pass",
        proposal_seq=run.board.seq("trader/proposal"),
        thesis_seq=run.board.seq("manager/thesis"),
        analysis_seq=run.board.seq("technical/analysis")), by="audit")
    _gate(run, "audit", [{"check": f.check, "subject": f.subject, "message": f.message, "owner": f.owner}
                         for f in findings])
    return findings


def audited_current(run: Run) -> bool:
    """黑板上的当前读数、论点、方案，是否就是审计通过的那一版（比较 audit/status 里记录的序号）。

    有通道分析时（audit/channels 存在），每个通道分析的当前序号也要与审计时一致（18.8）。
    """
    board = run.board
    if not board.has("audit/status"):
        return False
    status = board.get("audit/status", AuditStatus)
    if not (status.verdict == "pass" and status.proposal_seq == board.seq("trader/proposal")
            and status.thesis_seq == board.seq("manager/thesis")
            and status.analysis_seq == board.seq("technical/analysis")):
        return False
    if board.has("audit/channels"):
        return all(entry.seq == board.seq(entry.key)
                   for entry in board.get("audit/channels", list[AnalysisSeq]))
    return True


# ---- 报告 ----

def report(run: Run, deps: Deps) -> None:
    """把黑板写成 board.json，并渲染成 report.md。只读黑板，不做任何计算。"""
    if deps.out_dir is None or deps.render is None:
        return
    snapshot = run.board.snapshot()
    (deps.out_dir / "board.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (deps.out_dir / "report.md").write_text(deps.render(snapshot), encoding="utf-8")
