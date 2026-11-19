"""代码阶段的测试替身：最小的事实、最小的审计，只为让 analyze.py 的控制流可以被单独测试。

与 insightcrew/flows/stages.py 的函数名、参数和写入的键完全一致。test_flow.py 用它替换正式实现。
"""
from __future__ import annotations

from typing import Any

from insightcrew.domain.types import Action, Analysis, Fact, Finding, Proposal, Thesis
from insightcrew.kernel.runtime import Halt, Run


def intake(run: Run, request: dict[str, Any], deps: Any) -> None:
    if request.get("ticker") != "ACME":
        raise Halt("unknown_ticker", str(request.get("ticker")))
    run.board.put("request/scope", request, by="intake")


def ledger(run: Run, deps: Any) -> None:
    def f(i: str, v: float, u: str = "USD") -> Fact:
        return Fact(id=i, label=i, value=v, unit=u, as_of="2026-09-30", source="stub", bars_used=300)

    run.board.put("ledger/instrument", {"ticker": "ACME", "name": "Acme Corp", "currency": "USD"}, by="ledger")
    run.board.put("ledger/facts", [f("close", 100.0), f("atr_14", 2.0), f("rsi_14", 61.3, "")], by="ledger")
    run.board.put("rules/limits", [{"key": "max_risk_per_trade_pct", "limit": 1.0}], by="ledger")


def evidence(run: Run, deps: Any, channels: tuple[str, ...]) -> None:
    """证据阶段的双打：与真实存根一样，被点名的通道一律记为不可用（WP13 没有数据源）。"""
    for channel in channels:
        run.board.put(f"{channel}/status",
                      {"channel": channel, "state": "unavailable", "reason": "尚未接入数据源"},
                      by="evidence")
    run.journal.append("gate.checked", actor="evidence", data={"gate": "evidence", "findings": 0})


def channel_ready(run: Run, channel: str) -> bool:
    if not run.board.has(f"{channel}/status"):
        return False
    return run.board.get(f"{channel}/status")["state"] == "available"


def _finding(check: str, layer: Any, owner: Any, subject: str, message: str,
             limit: float | None = None, actual: float | None = None) -> Finding:
    return Finding(check=check, layer=layer, owner=owner, subject=subject, message=message,
                   limit=limit, actual=actual)


def _facts(run: Run) -> dict[str, float]:
    return {r["id"]: r["value"] for r in run.board.get("ledger/facts")}


def audit(run: Run, deps: Any) -> list[Finding]:
    facts = _facts(run)
    analysis = run.board.get("technical/analysis", Analysis)
    thesis = run.board.get("manager/thesis", Thesis)
    p = run.board.get("trader/proposal", Proposal)
    out: list[Finding] = []
    for i, c in enumerate(analysis.claims):
        if c.fact not in facts:
            out.append(_finding("claim_cites_known_fact", "L2", "technical", f"claims[{i}]", "引用了不存在的事实"))
        elif abs(c.value - facts[c.fact]) > max(0.005 * abs(facts[c.fact]), 0.01):
            out.append(_finding("claim_value_matches_fact", "L2", "technical", f"claims[{i}]",
                                "数值与事实不符", facts[c.fact], c.value))
    claimed = {c.fact for c in analysis.claims}
    for i, pt in enumerate(thesis.points):
        if not set(pt.cites) <= claimed:
            out.append(_finding("thesis_cites_claims", "L2", "manager", f"points[{i}]", "引用了分析师未陈述的事实"))
    if p.action is Action.BUY:
        if p.entry is None or p.stop is None:
            out.append(_finding("stop_required", "L3", "trader", "proposal.stop", "买入必须给出入场价和止损价"))
        elif p.stop >= p.entry:
            out.append(_finding("stop_side", "L3", "trader", "proposal.stop", "止损必须低于入场价"))
        else:
            risk = (p.entry - p.stop) / p.entry * p.size_pct
            if risk > 1.0:
                out.append(_finding("max_risk_per_trade_pct", "L3", "trader", "proposal", "单笔风险超限", 1.0, risk))
    for owner in ("technical", "manager", "trader"):
        run.board.put(f"audit/{owner}", [x for x in out if x.owner == owner], by="auditor")
    run.board.put("audit/findings", out, by="auditor")
    run.board.put("audit/status", {
        "verdict": "pass" if not out else "revise",
        "proposal_seq": run.board.seq("trader/proposal"),
        "thesis_seq": run.board.seq("manager/thesis"),
        "analysis_seq": run.board.seq("technical/analysis")}, by="auditor")
    run.journal.append("gate.checked", actor="auditor", data={"gate": "audit", "findings": len(out)})
    return out


def audited_current(run: Run) -> bool:
    """黑板上的当前方案，是否就是审计通过的那一版。"""
    b = run.board
    if not b.has("audit/status"):
        return False
    s = b.get("audit/status")
    return (s["verdict"] == "pass" and s["proposal_seq"] == b.seq("trader/proposal")
            and s["thesis_seq"] == b.seq("manager/thesis")
            and s["analysis_seq"] == b.seq("technical/analysis"))


def report(run: Run, deps: Any) -> None:
    pass
