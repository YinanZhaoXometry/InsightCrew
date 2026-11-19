"""流程控制的测试。代码阶段用 fake_stages 替换，所以这里只验证 analyze.py 的控制流。"""
import pytest

from insightcrew.adapters.scripted import Replay, Script
from insightcrew.flows import analyze as flow
from insightcrew.flows.analyze import FlowConfig, analyze
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget, Ceiling
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

from . import fake_stages

CLAIM = {"fact": "rsi_14", "value": 61.3, "reading": "动量偏强"}
BAD_CLAIM = {**CLAIM, "value": 31.3}
ANALYSIS = {"claims": [CLAIM], "narrative": "趋势向上"}
ARG = {"points": [{"text": "动量支持", "cites": ["rsi_14"]}], "rebuttal": ""}
THESIS = {"rating": "Buy", "horizon_days": 20, "points": [{"text": "动量偏强", "cites": ["rsi_14"]}],
          "invalidation": ["跌破 96"], "summary": "买入"}
GOOD = {"action": "Buy", "entry": 100.0, "stop": 96.0, "target": 108.0, "size_pct": 20.0,
        "horizon_days": 20, "cites": ["close", "atr_14"], "rationale": "止损在两倍 ATR 之外"}
RISKY = {**GOOD, "size_pct": 50.0}           # (100-96)/100*50 = 2% > 1%
WRONG_SIDE = {**GOOD, "stop": 104.0}
YES = {"accept": True, "target": "none", "critique": "", "cites": []}
NO_LEVELS = {"accept": False, "target": "levels", "critique": "止损太近", "cites": ["atr_14"]}
NO_THESIS = {"accept": False, "target": "thesis", "critique": "论点不成立", "cites": ["rsi_14"]}

BASE = {"technical": [ANALYSIS], "bull": [ARG], "bear": [ARG], "manager": [THESIS], "trader": [GOOD],
        "judge.evidence": [YES], "judge.conservative": [YES], "judge.aggressive": [YES]}
REQUEST = {"ticker": "ACME", "as_of": "2026-09-30"}


@pytest.fixture(autouse=True)
def _use_fake_stages(monkeypatch):
    monkeypatch.setattr(flow, "stages", fake_stages)


async def go(replies=None, cfg=None, request=None, ceiling=None, groups=None):
    journal = Journal()
    run = Run("t", journal, Board(journal), Script({**BASE, **(replies or {})}),
              Budget(ceiling, groups=groups))
    outcome = await analyze(run, request or REQUEST, None, cfg)
    calls = [e.actor for e in journal.events if e.type == "model.called"]
    return outcome, calls, run


async def test_clean_pass_stops_judges_at_quorum():
    o, calls, run = await go()
    assert (o.status, o.facts_and_rules, o.judgment) == ("delivered", "verified", "accepted 2/3")
    assert calls == ["technical", "bull", "bear", "manager", "trader", "judge.evidence", "judge.conservative"]
    assert run.journal.events[-1].type == "run.finished"


async def test_rule_break_goes_back_to_trader_only():
    o, calls, run = await go({"trader": [RISKY, GOOD]})
    assert o.status == "delivered"
    assert calls[:6] == ["technical", "bull", "bear", "manager", "trader", "trader"]
    second = [e for e in run.journal.events if e.type == "model.called" and e.actor == "trader"][1]
    assert "max_risk_per_trade_pct" in second.data["prompt"]
    assert run.board.get("audit/trader") == []


async def test_misquote_reruns_everything_downstream():
    o, calls, _ = await go({"technical": [{"claims": [BAD_CLAIM], "narrative": "超卖"}, ANALYSIS]})
    assert o.status == "delivered"
    assert calls[:10] == ["technical", "bull", "bear", "manager", "trader"] * 2


async def test_findings_for_two_owners_restart_from_the_earliest():
    o, calls, _ = await go({"technical": [{"claims": [BAD_CLAIM], "narrative": "超卖"}, ANALYSIS],
                            "trader": [RISKY, GOOD]})
    assert o.status == "delivered"
    assert calls[:10] == ["technical", "bull", "bear", "manager", "trader"] * 2    # 不是只重跑交易员


async def test_same_issue_twice_is_stagnation():
    o, calls, _ = await go({"trader": [RISKY]})
    assert (o.status, o.reason, o.facts_and_rules) == ("review", "stagnation", "unverified")
    assert calls.count("trader") == 2
    assert not any(c.startswith("judge") for c in calls)


async def test_different_issues_until_cap_is_audit_failed():
    o, calls, _ = await go({"trader": [RISKY, WRONG_SIDE, RISKY, WRONG_SIDE]})
    assert (o.status, o.reason) == ("review", "audit_failed")
    assert calls.count("trader") == 4


async def test_dissent_on_levels_revises_trader_and_reaudits():
    o, calls, run = await go({"trader": [GOOD, {**GOOD, "stop": 95.0, "size_pct": 15.0}],
                              "judge.evidence": [NO_LEVELS, YES], "judge.conservative": [NO_LEVELS, YES]})
    assert (o.status, o.judgment) == ("delivered", "accepted 2/3")
    assert calls[5:] == ["judge.evidence", "judge.conservative", "trader", "judge.evidence", "judge.conservative"]
    assert len([e for e in run.journal.events if e.type == "gate.checked"]) == 2
    assert run.board.get("judges/on_levels") == []


async def test_dissent_on_thesis_reruns_manager_and_trader():
    o, calls, _ = await go({"judge.evidence": [NO_THESIS, YES], "judge.conservative": [NO_LEVELS, YES]})
    assert o.status == "delivered"
    assert calls[5:9] == ["judge.evidence", "judge.conservative", "manager", "trader"]


async def test_contested_is_review_without_rating():
    o, calls, _ = await go({"judge.evidence": [NO_LEVELS], "judge.conservative": [NO_LEVELS]})
    assert (o.status, o.reason, o.facts_and_rules, o.judgment) == ("review", "contested", "verified", "contested")
    assert calls.count("judge.evidence") == 2


async def test_revision_that_breaks_a_rule_never_reaches_judges_again():
    o, calls, _ = await go({"trader": [GOOD, RISKY, RISKY],
                            "judge.evidence": [NO_LEVELS], "judge.conservative": [NO_LEVELS]})
    assert (o.status, o.reason) == ("review", "stagnation")
    assert calls.count("judge.evidence") == 1


async def test_malformed_judge_counts_as_dissent():
    o, _, run = await go({"judge.evidence": ["乱码"], "judge.conservative": [YES], "judge.aggressive": [YES]})
    assert o.judgment == "accepted 2/3"
    event = next(e for e in run.journal.events if e.type == "model.called" and e.actor == "judge.evidence")
    assert event.data["usage"]["requests"] == 2                   # 一次纠正机会
    assert not run.board.get("judge.evidence/verdict")["accept"]


async def test_malformed_worker_is_review():
    o, _, _ = await go({"manager": ["乱码"]})
    assert (o.status, o.reason) == ("review", "malformed_output")


async def test_out_of_scope_makes_no_model_call():
    o, calls, _ = await go(request={"ticker": "NOPE"})
    assert (o.status, o.reason, calls) == ("review", "unknown_ticker", [])


async def test_budget_before_audit_is_review():
    o, calls, run = await go(ceiling=Ceiling(tokens=3000))             # 每次调用 1300
    assert (o.status, o.reason, o.facts_and_rules) == ("review", "budget_exhausted", "unverified")
    assert len(calls) == 3
    assert sum(e.type == "budget.exceeded" for e in run.journal.events) == 1


async def test_budget_after_audit_delivers_unreviewed():
    o, calls, _ = await go(ceiling=Ceiling(tokens=6000))               # 五次调用后 6500 > 6000
    assert (o.status, o.reason, o.facts_and_rules, o.judgment) == (
        "delivered", "budget_exhausted", "verified", "not reviewed: budget_exhausted")
    assert len(calls) == 5


async def test_judge_group_ceiling_cuts_judges_only():
    o, calls, _ = await go(groups={"judge": 1000})
    assert (o.status, o.judgment) == ("delivered", "not reviewed: budget_exhausted")
    assert calls.count("judge.evidence") == 1


async def test_budget_during_judge_revision_is_review():
    o, _, _ = await go({"judge.evidence": [NO_LEVELS], "judge.conservative": [NO_LEVELS]},
                       ceiling=Ceiling(tokens=9000))                   # 七次调用后 9100 > 9000
    assert (o.status, o.reason, o.judgment) == ("review", "budget_exhausted", "not reviewed")


async def test_model_outage_after_audit_delivers_unreviewed():
    o, _, run = await go({"judge.evidence": [], "judge.conservative": [], "judge.aggressive": []})
    assert (o.status, o.reason) == ("delivered", "model_unavailable")
    assert o.judgment == "not reviewed: model_unavailable"
    assert sum(e.type == "model.failed" for e in run.journal.events) == 1


async def test_shadow_audit_records_but_does_not_route():
    o, calls, run = await go({"trader": [RISKY]}, FlowConfig(audit="shadow", judges=False))
    assert (o.status, o.facts_and_rules) == ("delivered", "unverified")
    assert calls.count("trader") == 1
    assert len(run.board.get("audit/findings")) == 1


async def test_replay_reproduces_a_run_from_its_journal():
    o1, _, run1 = await go({"trader": [RISKY, GOOD], "manager": ["乱码", THESIS],
                            "judge.evidence": [NO_LEVELS, YES]})
    journal = Journal()
    run2 = Run("t2", journal, Board(journal), Replay(run1.journal.events), Budget())
    o2 = await analyze(run2, REQUEST, None)
    assert o2 == o1
    assert run2.board.snapshot() == run1.board.snapshot()
    assert [e.type for e in journal.events] == [e.type for e in run1.journal.events]


# ---- 证据通道（BLUEPRINT 18.8）：关着的时候一个键都不写；不可用的通道不发生模型调用 ----


async def test_no_channel_writes_the_artifacts_of_version_20():
    """channels 为空：黑板、事件类型序列、每个提示词都与 2.0 相同（兼容规则 18.3）。"""
    o1, calls1, run1 = await go()
    o2, calls2, run2 = await go(cfg=FlowConfig(channels=()))
    assert o2 == o1 and calls2 == calls1
    assert run2.board.snapshot() == run1.board.snapshot()
    assert [e.type for e in run2.journal.events] == [e.type for e in run1.journal.events]
    prompts1 = [e.data["prompt"] for e in run1.journal.events if e.type == "model.called"]
    prompts2 = [e.data["prompt"] for e in run2.journal.events if e.type == "model.called"]
    assert prompts2 == prompts1
    assert not any(k.startswith(("fundamentals/", "disclosures/")) for k in run2.board.snapshot())
    assert not any(e.type == "gate.checked" and e.data.get("gate") == "evidence"
                   for e in run2.journal.events)


async def test_unavailable_channels_make_no_model_call_and_behave_like_20():
    """两个通道都被点名，但 evidence 判它们不可用：两个分析师都不被调用，结果与 2.0 相同。"""
    o, calls, run = await go(cfg=FlowConfig(channels=("fundamentals", "disclosures")))
    assert (o.status, o.facts_and_rules, o.judgment) == ("delivered", "verified", "accepted 2/3")
    assert calls == ["technical", "bull", "bear", "manager", "trader", "judge.evidence",
                     "judge.conservative"]                        # 7 次，与 clean 相同
    assert run.board.get("fundamentals/status")["state"] == "unavailable"
    assert run.board.get("disclosures/status")["state"] == "unavailable"
    assert not run.board.has("fundamentals/facts") and not run.board.has("disclosures/items")
    assert any(e.type == "gate.checked" and e.data.get("gate") == "evidence"
               for e in run.journal.events)
