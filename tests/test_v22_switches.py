"""版本 2.2 的开关（BLUEPRINT 19.18 WP31）：全部关 = 19.3 的兼容规则；
逐个打开 = 各自桩的状态出现。真阶段、真依赖（合成宇宙）、剧本模型。
"""
from __future__ import annotations

import json
from pathlib import Path

from insightcrew.adapters.scripted import Script
from insightcrew.flows.analyze import FlowConfig, analyze
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

_CLEAN = json.loads((Path(__file__).parent.parent / "data" / "scenarios" / "clean.json")
                    .read_text(encoding="utf-8"))
REPLIES, REQUEST = _CLEAN["replies"], _CLEAN["request"]
V22_KEYS = ("portfolio/", "risk/", "news/", "sentiment/", "macro/", "memory/", "tools/")


def _deps():                     # 真依赖：合成宇宙的行情、声明与各端口
    from insightcrew.app.settings import load
    from insightcrew.app.wiring import ROOT, build_deps
    return build_deps(load(ROOT))


async def _run(cfg: FlowConfig, deps, replies: dict | None = None):
    journal = Journal()
    run = Run("t", journal, Board(journal), Script({**REPLIES, **(replies or {})}), Budget())
    outcome = await analyze(run, REQUEST, deps, cfg)   # 工具由图的 intake 节点在 scope 落板后建
    return outcome, run


async def test_all_off_matches_the_21_shape():
    """全部关：两次运行的板与事件类型相同（19.3），且没有任何 v2.2 的键。"""
    deps = _deps()
    _, run1 = await _run(FlowConfig(), deps)
    _, run2 = await _run(FlowConfig(), deps)
    assert run1.board.snapshot() == run2.board.snapshot()
    assert [e.type for e in run1.journal.events] == [e.type for e in run2.journal.events]
    assert not any(k.startswith(V22_KEYS) for k in run1.board.snapshot())


async def test_portfolio_on_writes_input_and_state():
    """flat_usd：无持仓、现金充足，8% 的新仓在所有组合规则限内，照常交付；组合两键上板。

    （clean 原方案的 5% 也可以；held_acme 现金仅 10%，任何买入都违反 cash_sufficient——
    那是 WP32 的 portfolio_low_cash 场景。）
    """
    import dataclasses

    from insightcrew.adapters.portfolio_json import load_portfolio

    flat = load_portfolio(Path(__file__).parent.parent / "data" / "portfolios" / "flat_usd.json")
    smaller = [{**REPLIES["trader"][0], "size_pct": 8.0}]    # 有组合时 max_position_pct 生效
    outcome, run = await _run(FlowConfig(), dataclasses.replace(_deps(), portfolio=flat),
                              replies={"trader": smaller})
    assert outcome.status == "delivered", outcome.reason
    board = run.board.snapshot()
    assert "portfolio/input" in board and "portfolio/state" in board
    assert board["portfolio/state"]["state"] == "flat"


async def test_memory_on_writes_none_known_until_records_exist():
    outcome, run = await _run(FlowConfig(memory=True), _deps())
    assert outcome.status == "delivered"
    board = run.board.snapshot()
    assert board["memory/lessons"] == []
    assert board["memory/status"]["state"] == "none_known"


async def test_news_and_macro_on_write_unavailable_statuses():
    outcome, run = await _run(FlowConfig(channels=("news", "macro")), _deps())
    assert outcome.status == "delivered"
    board = run.board.snapshot()
    assert board["news/status"]["state"] == "unavailable"        # WP34 的桩：尚未接入
    assert board["macro/status"]["state"] == "unavailable"
    actors = [e.actor for e in run.journal.events if e.type == "model.called"]
    assert "news" not in actors                                   # 不可用的通道不发生模型调用


async def test_sentiment_on_writes_unavailable_status():
    outcome, run = await _run(FlowConfig(channels=("sentiment",)), _deps())
    assert outcome.status == "delivered"
    assert run.board.snapshot()["sentiment/status"]["state"] == "unavailable"


async def test_risk_rounds_on_runs_the_stub_and_changes_nothing():
    _, off = await _run(FlowConfig(), _deps())
    outcome, on = await _run(FlowConfig(risk_rounds=1), _deps())
    assert outcome.status == "delivered"
    assert not any(k.startswith("risk/") for k in on.board.snapshot())   # WP32 的桩不写键
    assert [e.actor for e in on.journal.events if e.type == "model.called"] == \
        [e.actor for e in off.journal.events if e.type == "model.called"]


async def test_tools_on_uses_the_tool_variant_without_calls():
    outcome, run = await _run(FlowConfig(tools=True), _deps())
    assert outcome.status == "delivered"                      # 剧本不调工具，照常完成
    assert "tools/facts" not in run.board.snapshot()          # 没有调用就没有事实（WP33 测调用）
