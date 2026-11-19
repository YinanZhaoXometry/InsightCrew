"""独立审计：L2 核对数字与引用，L3 把规则手册的失败汇总成发现。

审计只读契约，是纯函数：不调模型、不看板、不读文件、不做 I/O。它刻意不读叙述文字——
自由文本里的数字不做核对，报告里的数字一律来自事实账本，所以模型没法用文字夹带数字。
发现按蓝图 6.7（证据通道按 18.10）的检查顺序给出；每条 Finding 的 owner 是能修它的角色，
subject 指出是哪个对象出了问题，message 是一句说明问题的中文。
"""
from __future__ import annotations

from .types import (
    Action,
    Analysis,
    DisclosureAnalysis,
    Fact,
    Finding,
    Item,
    Proposal,
    RuleResult,
    Thesis,
)

# 蓝图 6.7：论点持有期只允许 1 到 60 个交易日。
_HORIZON_MIN = 1
_HORIZON_MAX = 60

# claim 值与事实值的容差：max(0.005 × |事实值|, 0.01)。技术面与基本面同一容差。
_TOL_PCT = 0.005
_TOL_MIN = 0.01


def _fmt(x: float) -> str:
    """数字进中文消息之前去掉多余的尾零。"""
    return f"{x:.6g}"


def audit(facts: list[Fact], analysis: Analysis, thesis: Thesis, proposal: Proposal,
          rule_results: list[RuleResult], *,
          fundamentals: tuple[list[Fact], Analysis] | None = None,
          disclosures: tuple[list[Item], DisclosureAnalysis] | None = None) -> list[Finding]:
    """按蓝图 6.7 与 18.10 的顺序给出全部发现：先技术面 claim，再基本面 claim、公告信号，
    然后论点、持有期、提案，最后把每条 fail 的规则变成 L3 发现。

    fundamentals 与 disclosures 为 None 时（通道未开或未上黑板），行为与版本 2.0 完全一致。
    """
    known = {f.id: f for f in facts}
    fund_cited = {c.fact for c in fundamentals[1].claims} if fundamentals else set()
    item_cited = {s.item for s in disclosures[1].signals} if disclosures else set()
    return [
        *_claims(analysis, known),
        *(_fundamentals_claims(fundamentals) if fundamentals else []),
        *(_disclosure_signals(disclosures) if disclosures else []),
        *_points(thesis, analysis, fund_cited, item_cited),
        *_horizon(thesis),
        *_proposal(proposal, thesis, known),
        *_rules(rule_results),
    ]


def _claims(analysis: Analysis, known: dict[str, Fact]) -> list[Finding]:
    """前三个检查都落在 claim 上：数量、引用是否存在、数值是否与事实一致。"""
    out: list[Finding] = []
    if len(analysis.claims) < 3:
        out.append(Finding(check="analysis_has_claims", layer="L2", owner="technical",
                           subject="analysis",
                           message=f"技术分析只有 {len(analysis.claims)} 条 claim，少于要求的 3 条"))
    for i, claim in enumerate(analysis.claims):
        fact = known.get(claim.fact)
        if fact is None:
            out.append(Finding(check="claim_cites_known_fact", layer="L2", owner="technical",
                               subject=f"claims[{i}]",
                               message=f"claim 引用的事实 {claim.fact} 不在事实账本中"))
            continue
        tolerance = max(_TOL_PCT * abs(fact.value), _TOL_MIN)
        if abs(claim.value - fact.value) > tolerance:
            out.append(Finding(check="claim_value_matches_fact", layer="L2", owner="technical",
                               subject=f"claims[{i}]", limit=fact.value, actual=claim.value,
                               message=f"claim 声称 {claim.fact} 为 {_fmt(claim.value)}，"
                                       f"与事实值 {_fmt(fact.value)} 不符"))
    return out


def _fundamentals_claims(fundamentals: tuple[list[Fact], Analysis]) -> list[Finding]:
    """基本面 claim 的三项检查（蓝图 18.10）：数量、引用、数值，与技术面同一套容差。"""
    facts, analysis = fundamentals
    known = {f.id: f for f in facts}
    out: list[Finding] = []
    minimum = min(3, len(facts))
    if len(analysis.claims) < minimum:
        out.append(Finding(check="analysis_has_claims", layer="L2", owner="fundamentals",
                           subject="fundamentals.analysis",
                           message=f"基本面分析只有 {len(analysis.claims)} 条 claim，"
                                   f"少于要求的 {minimum} 条"))
    for i, claim in enumerate(analysis.claims):
        fact = known.get(claim.fact)
        if fact is None:
            out.append(Finding(check="claim_cites_known_fact", layer="L2", owner="fundamentals",
                               subject=f"fundamentals.claims[{i}]",
                               message=f"claim 引用的事实 {claim.fact} 不在基本面事实中"))
            continue
        tolerance = max(_TOL_PCT * abs(fact.value), _TOL_MIN)
        if abs(claim.value - fact.value) > tolerance:
            out.append(Finding(check="claim_value_matches_fact", layer="L2", owner="fundamentals",
                               subject=f"fundamentals.claims[{i}]", limit=fact.value, actual=claim.value,
                               message=f"claim 声称 {claim.fact} 为 {_fmt(claim.value)}，"
                                       f"与事实值 {_fmt(fact.value)} 不符"))
    return out


def _disclosure_signals(disclosures: tuple[list[Item], DisclosureAnalysis]) -> list[Finding]:
    """公告信号的两项检查（蓝图 18.10）：至少一条信号、信号引用的条目存在。

    方向读得对不对是判断，不由代码核对（18.10 末段）。
    """
    items, analysis = disclosures
    known = {i.id for i in items}
    out: list[Finding] = []
    if not analysis.signals:
        out.append(Finding(check="disclosures_has_signals", layer="L2", owner="disclosures",
                           subject="disclosures.analysis",
                           message="公告分析没有给出任何信号"))
    for i, signal in enumerate(analysis.signals):
        if signal.item not in known:
            out.append(Finding(check="signal_cites_known_item", layer="L2", owner="disclosures",
                               subject=f"disclosures.signals[{i}]",
                               message=f"信号引用的条目 {signal.item} 不在公告列表中"))
    return out


def _points(thesis: Thesis, analysis: Analysis,
            fund_cited: set[str], item_cited: set[str]) -> list[Finding]:
    """论点必须被 claim 或信号支撑：引用的 ID 要么是某个分析师 claim 过的事实 ID，
    要么是某个信号解读过的公告条目 ID（蓝图 18.10 的拓宽版）。"""
    cited = {c.fact for c in analysis.claims} | fund_cited | item_cited
    out: list[Finding] = []
    for i, point in enumerate(thesis.points):
        if not point.cites:
            out.append(Finding(check="point_cites_claimed_fact", layer="L2", owner="manager",
                               subject=f"points[{i}]",
                               message="论点没有引用任何证据，至少要引用一个被 claim 引用的事实 ID "
                                       "或被信号解读的公告条目 ID"))
            continue
        missing = next((fid for fid in point.cites if fid not in cited), None)
        if missing is not None:
            out.append(Finding(check="point_cites_claimed_fact", layer="L2", owner="manager",
                               subject=f"points[{i}]",
                               message=f"论点引用的 {missing} 没有出现在任何分析师的 claim 或公告信号中"))
    return out


def _horizon(thesis: Thesis) -> list[Finding]:
    if _HORIZON_MIN <= thesis.horizon_days <= _HORIZON_MAX:
        return []
    return [Finding(check="horizon_in_range", layer="L2", owner="manager",
                    subject="thesis.horizon_days",
                    message=f"论点持有期 {thesis.horizon_days} 个交易日不在 "
                            f"{_HORIZON_MIN} 到 {_HORIZON_MAX} 之间")]


def _proposal(proposal: Proposal, thesis: Thesis, known: dict[str, Fact]) -> list[Finding]:
    """Buy 提案必须引用事实；任何提案引用的 ID 都必须在账本里；持有期必须与论点一致。"""
    out: list[Finding] = []
    if proposal.action is Action.BUY and not proposal.cites:
        out.append(Finding(check="proposal_cites_known_fact", layer="L2", owner="trader",
                           subject="proposal.cites",
                           message="Buy 提案没有引用任何事实，必须给出数字依据"))
    for i, fact_id in enumerate(proposal.cites):
        if fact_id not in known:
            out.append(Finding(check="proposal_cites_known_fact", layer="L2", owner="trader",
                               subject=f"proposal.cites[{i}]",
                               message=f"提案引用的事实 {fact_id} 不在事实账本中"))
    if proposal.horizon_days != thesis.horizon_days:
        out.append(Finding(check="horizon_matches_thesis", layer="L2", owner="trader",
                           subject="proposal.horizon_days",
                           message=f"提案持有期 {proposal.horizon_days} 个交易日与论点的 "
                                   f"{thesis.horizon_days} 个交易日不一致"))
    return out


def _rules(rule_results: list[RuleResult]) -> list[Finding]:
    """每条 fail 的规则变成一条 L3 发现，交给交易员去修。"""
    out: list[Finding] = []
    for r in rule_results:
        if r.status != "fail":
            continue
        subject = "proposal.stop" if r.key in ("stop_required", "stop_side") else "proposal"
        out.append(Finding(check=r.key, layer="L3", owner="trader", subject=subject,
                           message=r.message, limit=r.limit, actual=r.actual))
    return out
