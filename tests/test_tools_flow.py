"""工具进流程的验收测试（WP33；BLUEPRINT 19.11）。

剧本学会工具调用（adapters/scripted.py）之后，场景能离线演练完整的回路：
技术分析师先发 {"tool": "indicator", "args": ...}，内核包装真的执行工具、把结果送回，
剧本再给出最终输出。tools_out_of_range 全程可跑：被拒的窗口不产生事实，分析照常交付；
回放把录制时的工具调用重放一遍，工具再次执行、又给出同一个答案。

协调者已按共享文件请求修复 tools/facts 的形状（板键存完整 Fact，返回给模型的仍是
四键结果），下列各项常态运行。
"""
import json
from functools import partial
from pathlib import Path

from insightcrew.adapters.scripted import Replay, Script
from insightcrew.app.settings import load
from insightcrew.app.wiring import ROOT, build_deps, open_run
from insightcrew.evals.scenarios import load_one, run_one
from insightcrew.flows.analyze import analyze
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

SETTINGS = load(ROOT, force_offline=True)
SMA_30 = 217.70366666666666      # indicator("sma", 30) 在 ACME 2026-09-30 的真值（工具现算）
_GAP = ("tools/facts 形状缺口：flows/tools.py 收集四键结果、flows/stages.py 的 audit 按 "
        "list[Fact] 校验（HANDOFF 第 7 节共享文件请求，WP33 记）")


def called_of(run: Run, actor: str):
    return next(e for e in run.journal.events if e.type == "model.called" and e.actor == actor)


# ---- 蓝图点名的两个场景 ----

async def test_the_out_of_range_scenario_matches(tmp_path: Path) -> None:
    """tools_out_of_range：n=9999 超范围，错误说明送回，分析不引用它，照常交付。"""
    scenario = load_one("tools_out_of_range")
    opener = partial(open_run, SETTINGS, runs_dir=tmp_path)
    result = await run_one(scenario, opener, analyze)
    assert result.ok, result.detail


async def test_the_flow_runs_the_scripted_tool_call_through_the_real_tool() -> None:
    """直接驱动一次带工具调用的运行：工具真的执行、错误说明送回模型、不产生工具事实。"""
    scenario = load_one("tools_out_of_range")
    journal = Journal()
    run = Run("t", journal, Board(journal), Script(dict(scenario.replies)), Budget())
    outcome = await analyze(run, dict(scenario.request), build_deps(SETTINGS),
                            scenario.config.flow)
    assert (outcome.status, outcome.facts_and_rules) == ("delivered", "verified")
    event = called_of(run, "technical")
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (1, 0)
    assert event.data["usage"]["requests"] == 2            # 一次工具调用 + 一次最终输出
    sent = json.dumps(event.data["messages"], ensure_ascii=False)
    assert "超出允许范围 2 到 250" in sent                 # 工具的错误说明原样送回了模型
    assert "数据不可用，不要编造。" in sent
    assert "sma_9999" not in sent                          # 被拒的调用没有变成任何事实
    assert "tools/facts" not in run.board.snapshot()       # 没有成功调用就没有这个键


async def test_a_recorded_run_with_a_tool_call_replays_identically() -> None:
    """带工具调用的运行先跑一遍，再用 Replay 重放：板与事件一致，工具再次执行。"""
    scenario = load_one("tools_out_of_range")
    deps = build_deps(SETTINGS)
    j1 = Journal()
    run1 = Run("t1", j1, Board(j1), Script(dict(scenario.replies)), Budget())
    o1 = await analyze(run1, dict(scenario.request), deps, scenario.config.flow)

    j2 = Journal()
    run2 = Run("t2", j2, Board(j2), Replay(j1.events), Budget())
    o2 = await analyze(run2, dict(scenario.request), deps, scenario.config.flow)
    assert o2 == o1
    assert run2.board.snapshot() == run1.board.snapshot()
    assert [e.type for e in j2.events] == [e.type for e in j1.events]
    second = called_of(run2, "technical")
    assert second.data["tool_calls"] == 1                  # 重放里工具又真的执行了一次
    assert "超出允许范围 2 到 250" in json.dumps(
        second.data["messages"], ensure_ascii=False)       # 同一个错误说明又送回了模型


# ---- 缺口已由协调者修复（tools/facts 存完整 Fact）：以下两项常态运行 ----

async def test_the_clean_tools_scenario_matches(tmp_path: Path) -> None:
    """tools_clean：一次成功的工具调用，分析引用 sma_30，照常交付。"""
    scenario = load_one("tools_clean")
    opener = partial(open_run, SETTINGS, runs_dir=tmp_path)
    result = await run_one(scenario, opener, analyze)
    assert result.ok, result.detail


async def test_a_successful_tool_call_sends_the_fact_back_and_collects_it() -> None:
    """成功的调用把真值送回模型，事实按调用顺序上板 tools/facts 供审计查证。"""
    scenario = load_one("tools_clean")
    journal = Journal()
    run = Run("t", journal, Board(journal), Script(dict(scenario.replies)), Budget())
    outcome = await analyze(run, dict(scenario.request), build_deps(SETTINGS),
                            scenario.config.flow)
    assert outcome.status == "delivered"
    event = called_of(run, "technical")
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (1, 0)
    assert str(SMA_30) in json.dumps(
        event.data["messages"], ensure_ascii=False)       # 工具算出的 sma_30 真值送回了模型
    facts = run.board.get("tools/facts")                  # 不论四键还是七键，事实就是那一条
    assert [f["id"] for f in facts] == ["sma_30"]
    assert facts[0]["value"] == SMA_30
