"""WP32（BLUEPRINT 19.18）：组合与风险辩论进入流程。

两层检查（全部离线，剧本模型）：
- 五个场景（data/scenarios/portfolio_*、risk_*）经 evals.scenarios 走与
  `insightcrew scenarios` 同一条路径，并直接驱动 analyze 核对黑板上的数字：
  组合四态折算、审计打回与修订后的方案；
- stage_risk 的直接测试：每轮的发言顺序必须是 激进、保守、中立、交易员（19.10）。
"""
from __future__ import annotations

import dataclasses
import tempfile
from functools import partial
from pathlib import Path

import pytest

from insightcrew.adapters.scripted import Script
from insightcrew.domain.portfolio import Portfolio
from insightcrew.domain.types import Action, Proposal, Rating, Thesis
from insightcrew.evals.scenarios import load_one, run_all
from insightcrew.flows.analyze import FlowConfig, analyze
from insightcrew.flows.stage_risk import risk as risk_stage
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

_FIVE = ("portfolio_flat_sell", "portfolio_held_sell", "portfolio_low_cash",
         "risk_shrinks_size", "risk_off_same")


def _deps():
    """真依赖：合成宇宙的行情、声明与各端口（与 tests/test_v22_switches.py 相同的装法）。"""
    from insightcrew.app.settings import load
    from insightcrew.app.wiring import ROOT, build_deps
    return build_deps(load(ROOT))


async def _drive(name: str):
    """按场景文件直接驱动 analyze：内联组合换成 deps.portfolio（wiring 的同一条路）。"""
    scenario = load_one(name)
    deps = _deps()
    if scenario.config.portfolio:
        deps = dataclasses.replace(deps, portfolio=Portfolio.model_validate(scenario.config.portfolio))
    journal = Journal()
    run = Run("t", journal, Board(journal), Script(scenario.replies), Budget())
    outcome = await analyze(run, dict(scenario.request), deps, scenario.config.flow)
    return outcome, run


def _actors(run: Run) -> list[str]:
    return [e.actor for e in run.journal.events if e.type == "model.called"]


# ---- 五个场景：与 `insightcrew scenarios` 同一条路径 ----


async def test_the_five_scenarios_match():
    """五个场景全部 ✓ 相符（19.9 与 19.10 各自的验收）。"""
    from insightcrew.app.settings import load
    from insightcrew.app.wiring import ROOT, open_run, run_flow
    settings = load(ROOT)
    with tempfile.TemporaryDirectory() as scratch:          # 场景是一次检查，不进 runs/
        opener = partial(open_run, settings, runs_dir=Path(scratch))
        results = await run_all(opener, run_flow, list(_FIVE))
    assert all(r.ok for r in results), [f"{r.name}: {r.detail}" for r in results]


# ---- 组合：四态折算与规则打回 ----


async def test_flat_portfolio_refuses_the_sell_then_revises_to_hold():
    """flat：portfolio/state 为空仓（现金 100%）；Sell 被 sell_requires_position 打回，
    修订为 Hold 后交付，路由事件恰好一次、目标是交易员。"""
    outcome, run = await _drive("portfolio_flat_sell")
    assert outcome.status == "delivered"
    state = run.board.snapshot()["portfolio/state"]
    assert state["state"] == "flat" and state["quantity"] == 0.0
    assert state["position_pct"] == 0.0 and state["cash_pct"] == 100.0
    routes = [e for e in run.journal.events if e.type == "route"]
    assert [(e.data["to"], e.data["why"]) for e in routes] == [("trader", "审计发现 1 个问题")]
    proposal = run.board.get("trader/proposal", Proposal)
    assert proposal.action.value == "Hold" and proposal.size_pct == 0.0
    assert run.board.get("audit/findings", list) == []      # 修订后的审计没有发现


async def test_held_portfolio_state_and_delivered_sell():
    """held：90 股按收盘 230.85 折算 position_pct 恰为 207.765，现金 10%；Sell 50% 一次通过。"""
    outcome, run = await _drive("portfolio_held_sell")
    assert outcome.status == "delivered"
    state = run.board.snapshot()["portfolio/state"]
    assert state["state"] == "held" and state["quantity"] == 90.0
    assert state["position_pct"] == pytest.approx(207.765) and state["cash_pct"] == 10.0
    assert not [e for e in run.journal.events if e.type == "route"]   # 无打回
    proposal = run.board.get("trader/proposal", Proposal)
    assert proposal.action.value == "Sell" and proposal.size_pct == 50.0


async def test_low_cash_cuts_the_buy_to_the_cash_line():
    """现金只占 1%：5% 的买入违反 cash_sufficient，打回后改小到 1%（恰好等于现金占比）交付。"""
    outcome, run = await _drive("portfolio_low_cash")
    assert outcome.status == "delivered"
    state = run.board.snapshot()["portfolio/state"]
    assert state["state"] == "flat" and state["cash_pct"] == 1.0      # ACME 无持仓（仓位在 SYNUS）
    first = run.board.history("trader/proposal")
    assert len(first) == 2 and first[0]["size_pct"] == 5.0 and first[1]["size_pct"] == 1.0
    rules = [r for version in run.board.history("audit/rules") for r in version]
    cash = [r for r in rules if r["key"] == "cash_sufficient"]
    assert [r["status"] for r in cash] == ["fail", "pass"]
    assert cash[0]["limit"] == 1.0 and cash[0]["actual"] == 5.0


# ---- 风险辩论：顺序与修订 ----


async def test_risk_shrinks_size_adopts_the_conservative_view():
    """risk_rounds=1：三份意见上板，交易员采纳保守方把仓位从 5% 降到 3%，交付。"""
    outcome, run = await _drive("risk_shrinks_size")
    assert outcome.status == "delivered"
    assert _actors(run)[-6:] == ["risk.aggressive", "risk.conservative", "risk.neutral",
                                 "trader", "judge.evidence", "judge.conservative"]
    board = run.board.snapshot()
    assert sorted(k for k in board if k.startswith("risk/")) == [
        "risk/aggressive", "risk/conservative", "risk/neutral"]
    assert board["risk/conservative"]["size_pct"] == 3.0
    versions = run.board.history("trader/proposal")
    assert len(versions) == 2 and versions[1]["size_pct"] == 3.0      # 修订版采纳了保守意见


async def test_risk_off_same_gives_the_board_of_clean():
    """risk_rounds=0：板与调用序列与 clean 完全相同（19.10 的 risk_off_same，即 19.3）。"""
    _, off = await _drive("risk_off_same")
    _, clean = await _drive("clean")
    assert off.board.snapshot() == clean.board.snapshot()
    assert _actors(off) == _actors(clean)
    assert not any(k.startswith("risk/") for k in off.board.snapshot())


# ---- stage_risk 直接测试：每轮的发言顺序 ----


def _seeded_run(replies: dict) -> Run:
    """给 stage_risk 一个最小的黑板：三位风控与交易员要读的论点与方案已就位。"""
    journal = Journal()
    run = Run("t", journal, Board(journal), Script(replies), Budget())
    run.board.put("manager/thesis", Thesis(
        rating=Rating.BUY, horizon_days=20,
        points=(), invalidation=("收盘跌破止损位",), summary="顺势做多"), by="test")
    run.board.put("trader/proposal", Proposal(
        action=Action.BUY, entry=230.85, stop=225.96826420857275, target=241.10164516199723,
        size_pct=5.0, horizon_days=20, cites=("close", "atr_14"), rationale="初版"), by="test")
    return run


def _risk_replies() -> dict:
    """三位风控各一份意见（剧本用完重复最后一条），交易员一份修订。"""
    replies: dict = {"trader": [{"action": "Buy", "entry": 230.85, "stop": 225.96826420857275,
                                 "target": 241.10164516199723, "size_pct": 5.0,
                                 "horizon_days": 20, "cites": ["close", "atr_14"],
                                 "rationale": "维持方案"}]}
    for stance in ("aggressive", "conservative", "neutral"):
        replies[f"risk.{stance}"] = [{"stance": stance, "critique": "意见",
                                      "size_pct": None, "stop": None, "target": None,
                                      "cites": ["close"], "rebuttal": ""}]
    return replies


def test_stage_risk_speaks_in_order_each_round():
    """两轮：每轮都是 激进、保守、中立、交易员；意见写上板，方案每轮修订一次。"""
    run = _seeded_run(_risk_replies())
    risk_stage(run, _deps(), FlowConfig(risk_rounds=2))     # 同步直调：不在事件循环里
    assert _actors(run) == ["risk.aggressive", "risk.conservative", "risk.neutral", "trader"] * 2
    assert all(run.board.has(f"risk/{s}") for s in ("aggressive", "neutral", "conservative"))
    assert len(run.board.history("trader/proposal")) == 3   # 初版 + 两轮各修订一次


def test_stage_risk_with_zero_rounds_calls_nobody():
    """risk_rounds=0：本阶段一个模型调用也不发起（图本就不该加这个节点，这里钉死下限）。"""
    run = _seeded_run(_risk_replies())
    risk_stage(run, _deps(), FlowConfig(risk_rounds=0))
    assert _actors(run) == []
    assert not run.board.has("risk/aggressive")
