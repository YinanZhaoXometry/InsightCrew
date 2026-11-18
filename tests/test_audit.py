"""审计的测试：七个 L2 检查逐个触发，L3 把规则失败带出来，干净的案例没有任何发现。

事实、分析与提案全部手工构造；规则结果直接调 rules.evaluate 得到，所以 L2 到 L3 的衔接
也是真实路径。蓝图 12.3 的注入mutation与有效变体在这里各测一遍。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from insightcrew.domain import rules
from insightcrew.domain.audit import audit
from insightcrew.domain.types import (
    Action,
    Analysis,
    Claim,
    Fact,
    Finding,
    Point,
    Proposal,
    Rating,
    RuleResult,
    RuleSpec,
    Thesis,
)

_RULES_TOML = Path(__file__).parent.parent / "data" / "rules.toml"


def _fact(fid: str, value: float, label: str, unit: str) -> Fact:
    return Fact(id=fid, label=label, value=value, unit=unit, as_of="2026-09-30",
                source="indicators.test", bars_used=20)


_FACTS = [_fact("close", 100.0, "收盘价", "USD"),
          _fact("atr_14", 5.0, "14 日平均真实波幅", "USD"),
          _fact("rsi_14", 55.4321, "14 日相对强弱", "")]


def load_specs() -> tuple[RuleSpec, ...]:
    with _RULES_TOML.open("rb") as f:
        tables = tomllib.load(f)
    return tuple(RuleSpec(key=key, limit=table.get("limit"), formula=table["formula"],
                          desc=table["desc"], source=table["source"])
                 for key, table in tables.items())


def fact_map() -> dict[str, float]:
    return {f.id: f.value for f in _FACTS}


def _claim(fid: str, value: float) -> Claim:
    return Claim(fact=fid, value=value, reading="测试解读")


def clean_analysis() -> Analysis:
    return Analysis(claims=(_claim("close", 100.0),
                            _claim("atr_14", 5.0),
                            _claim("rsi_14", 55.43)),        # 两位小数的四舍五入值必须算通过
                    narrative="测试叙述")


def _point(text: str, *cites: str) -> Point:
    return Point(text=text, cites=cites)


def clean_thesis() -> Thesis:
    return Thesis(rating=Rating.BUY, horizon_days=10,
                  points=(_point("价格在均线上方", "close"),), invalidation=(), summary="测试论点")


def clean_proposal() -> Proposal:
    return Proposal(action=Action.BUY, entry=100.0, stop=95.0, target=107.5, size_pct=20.0,
                    horizon_days=10, cites=("close", "atr_14"), rationale="测试提案")


def clean_results(proposal: Proposal | None = None, thesis: Thesis | None = None) -> list[RuleResult]:
    return rules.evaluate(proposal or clean_proposal(), thesis or clean_thesis(),
                          fact_map(), load_specs())


def run_audit(analysis: Analysis | None = None, thesis: Thesis | None = None,
              proposal: Proposal | None = None, results: list[RuleResult] | None = None) -> list[Finding]:
    return audit(_FACTS, analysis or clean_analysis(), thesis or clean_thesis(),
                 proposal or clean_proposal(),
                 results if results is not None else clean_results(proposal, thesis))


def of_check(findings: list[Finding], check: str) -> list[Finding]:
    return [f for f in findings if f.check == check]


# ---- 干净案例：没有一个发现 ----


def test_clean_case_gives_no_findings():
    assert run_audit() == []


# ---- L2：三个 claim 检查 ----


def test_analysis_with_fewer_than_three_claims():
    two = Analysis(claims=(_claim("close", 100.0), _claim("atr_14", 5.0)), narrative="只有两条")
    found = of_check(run_audit(analysis=two), "analysis_has_claims")
    assert len(found) == 1
    assert found[0].owner == "technical" and found[0].subject == "analysis"


def test_claim_citing_an_unknown_fact():
    bad = Analysis(claims=(_claim("rsi_15", 50.0), _claim("close", 100.0), _claim("atr_14", 5.0)),
                   narrative="第一个 claim 引用了不存在的事实")
    found = of_check(run_audit(analysis=bad), "claim_cites_known_fact")
    assert len(found) == 1
    assert found[0].owner == "technical" and found[0].subject == "claims[0]"
    assert "rsi_15" in found[0].message


@pytest.mark.parametrize("bad_value", [103.0, 5.0])      # ×1.03；换成另一个事实的值
def test_claim_value_that_does_not_match_the_fact(bad_value: float):
    bad = Analysis(claims=(_claim("close", bad_value), _claim("atr_14", 5.0), _claim("rsi_14", 55.43)),
                   narrative="第一个 claim 的数值与事实不符")
    found = of_check(run_audit(analysis=bad), "claim_value_matches_fact")
    assert len(found) == 1
    f = found[0]
    assert f.owner == "technical" and f.subject == "claims[0]"
    assert f.limit == 100.0 and f.actual == bad_value      # 事实值不隐瞒，让分析员看到该是多少


def test_a_rounded_claim_value_is_within_tolerance():
    # rsi_14 = 55.4321，声称 55.43：偏差 0.0021，远小于容差 max(0.5% × 55.43, 0.01)。
    assert of_check(run_audit(), "claim_value_matches_fact") == []


# ---- L2：论点与持有期 ----


def test_point_citing_a_fact_no_claim_cites():
    thesis = clean_thesis().model_copy(update={"points": (_point("MACD 背离", "macd"),)})
    found = of_check(run_audit(thesis=thesis), "point_cites_claimed_fact")
    assert len(found) == 1
    assert found[0].owner == "manager" and found[0].subject == "points[0]"


def test_point_citing_nothing():
    thesis = clean_thesis().model_copy(update={"points": (_point("没有引用",),)})
    found = of_check(run_audit(thesis=thesis), "point_cites_claimed_fact")
    assert len(found) == 1 and found[0].subject == "points[0]"


@pytest.mark.parametrize("horizon", [0, 500])
def test_thesis_horizon_out_of_range(horizon: int):
    thesis = clean_thesis().model_copy(update={"horizon_days": horizon})
    found = of_check(run_audit(thesis=thesis), "horizon_in_range")
    assert len(found) == 1
    assert found[0].owner == "manager" and found[0].subject == "thesis.horizon_days"


@pytest.mark.parametrize("horizon", [1, 60])
def test_thesis_horizon_boundaries_are_valid(horizon: int):
    thesis = clean_thesis().model_copy(update={"horizon_days": horizon})
    proposal = clean_proposal().model_copy(update={"horizon_days": horizon})
    assert run_audit(thesis=thesis, proposal=proposal) == []


# ---- L2：提案的引用与持有期 ----


def test_buy_proposal_without_cites():
    proposal = clean_proposal().model_copy(update={"cites": ()})
    found = of_check(run_audit(proposal=proposal), "proposal_cites_known_fact")
    assert len(found) == 1
    assert found[0].owner == "trader" and found[0].subject == "proposal.cites"


def test_proposal_citing_an_unknown_fact():
    proposal = clean_proposal().model_copy(update={"cites": ("nope",)})
    found = of_check(run_audit(proposal=proposal), "proposal_cites_known_fact")
    assert len(found) == 1
    assert found[0].owner == "trader" and found[0].subject == "proposal.cites[0]"


def test_sell_proposal_may_cite_nothing():
    sell = Proposal(action=Action.SELL, entry=None, stop=None, target=None, size_pct=0.0,
                    horizon_days=10, cites=(), rationale="清仓离场")
    findings = run_audit(proposal=sell)
    assert of_check(findings, "proposal_cites_known_fact") == []


def test_proposal_horizon_must_match_the_thesis():
    proposal = clean_proposal().model_copy(update={"horizon_days": 5})
    found = of_check(run_audit(proposal=proposal), "horizon_matches_thesis")
    assert len(found) == 1
    assert found[0].owner == "trader" and found[0].subject == "proposal.horizon_days"


# ---- L3：规则失败变成给交易员的发现 ----


def test_stop_above_entry_becomes_an_l3_finding_on_proposal_stop():
    proposal = clean_proposal().model_copy(update={"stop": 105.0})
    findings = run_audit(proposal=proposal)
    l3 = [f for f in findings if f.layer == "L3"]
    assert [f.check for f in l3] == ["stop_side"]
    f = l3[0]
    assert f.owner == "trader" and f.subject == "proposal.stop"
    assert f.limit is None and f.actual is None


def test_oversized_position_reports_rule_limit_and_actual():
    proposal = clean_proposal().model_copy(update={"size_pct": 25.0})   # 风险 1.25%，总仓位 25%
    findings = run_audit(proposal=proposal)
    l3 = {f.check: f for f in findings if f.layer == "L3"}
    assert set(l3) == {"max_risk_per_trade_pct", "max_position_pct"}
    assert l3["max_risk_per_trade_pct"].limit == 1.0
    assert l3["max_risk_per_trade_pct"].actual == pytest.approx(1.25)
    assert l3["max_position_pct"].limit == 20
    assert l3["max_position_pct"].actual == pytest.approx(25.0)


def test_findings_come_in_blueprint_order_with_a_message_each():
    analysis = Analysis(claims=(_claim("rsi_15", 50.0), _claim("close", 103.0)),
                        narrative="引用不存在的事实，又把收盘价放大 3%")
    thesis = clean_thesis().model_copy(update={"horizon_days": 0, "points": (_point("没有引用",),)})
    proposal = clean_proposal().model_copy(update={"horizon_days": 5, "cites": (), "stop": 105.0})
    findings = run_audit(analysis=analysis, thesis=thesis, proposal=proposal)
    assert [f.check for f in findings] == [
        "analysis_has_claims", "claim_cites_known_fact", "claim_value_matches_fact",
        "point_cites_claimed_fact", "horizon_in_range", "proposal_cites_known_fact",
        "horizon_matches_thesis", "stop_side"]
    assert all(f.message for f in findings)
