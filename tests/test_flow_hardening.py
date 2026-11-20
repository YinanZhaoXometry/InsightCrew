"""WP41（BLUEPRINT 20.7 第 1、3、4 条）：流程加固的复现测试。

先写测试、先看它挂，再修（20.1 的约定）。三个缺陷在这里各有一条：
- D1：风险辩论期间事件循环必须保持可用（stage_risk.risk 异步化之前，图的 risk 节点
  把整段辩论放进另一线程的新循环里跑，主循环被阻塞）；
- D3（流程这一半）：带工具的运行在 manager 之后被中断，续跑后审计打回到技术分析师时，
  run.tools 已随 intake 一起丢掉，内核取工具时 KeyError——续跑必须重建工具；
- 兄弟分支的花费：并行扇出里第二位分析师停机时，第一位已算完的结果要照样入账。

D2（扇出中途中断的续跑）在 tests/test_graph.py，与既有的续跑测试放在一起。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai import ModelAPIError
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel

from insightcrew.adapters.scripted import Script
from insightcrew.evals.scenarios import load_one
from insightcrew.flows.analyze import FlowConfig, analyze, drive
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Role, Run

REQUEST = {"ticker": "ACME", "as_of": "2026-09-30"}
_CHANNELS_FLOW = FlowConfig(channels=("fundamentals",))
_BASE_REPLIES = {"technical": [{"claims": [{"fact": "rsi_14", "value": 61.3, "reading": "动量偏强"}],
                                "narrative": "趋势向上"}],
                 "bull": [{"points": [{"text": "动量支持", "cites": ["rsi_14"]}], "rebuttal": ""}],
                 "bear": [{"points": [{"text": "动量支持", "cites": ["rsi_14"]}], "rebuttal": ""}],
                 "manager": [{"rating": "Buy", "horizon_days": 20,
                              "points": [{"text": "动量偏强", "cites": ["rsi_14"]}],
                              "invalidation": ["跌破 96"], "summary": "买入"}],
                 "trader": [{"action": "Buy", "entry": 100.0, "stop": 96.0, "target": 108.0,
                             "size_pct": 20.0, "horizon_days": 20, "cites": ["close", "atr_14"],
                             "rationale": "止损在两倍 ATR 之外"}],
                 "judge.evidence": [{"accept": True, "target": "none", "critique": "", "cites": []}],
                 "judge.conservative": [{"accept": True, "target": "none", "critique": "", "cites": []}]}
_FUNDAMENTALS = {"claims": [{"fact": "revenue_fy", "value": 1081.0, "reading": "收入扩大"}],
                 "narrative": "基本面稳健"}


def _deps():          # 真依赖：合成宇宙（与 tests/test_stage_portfolio_risk.py 相同的装法）
    from insightcrew.app.settings import load
    from insightcrew.app.wiring import ROOT, build_deps
    return build_deps(load(ROOT))


def _run(models: Any) -> Run:
    journal = Journal()
    return Run("t", journal, Board(journal), models, Budget())


def _actors(run: Run) -> list[str]:
    return [e.actor for e in run.journal.events if e.type == "model.called"]


class _CrashOnce:
    """模型端口替身：第一次问到 crash_on 角色时抛普通异常（模拟崩溃，不是 Halt）。"""

    def __init__(self, replies: dict, crash_on: str):
        self._script = Script(replies)
        self._crash_on, self._armed = crash_on, True

    def for_role(self, role: Role) -> Model:
        if role.name == self._crash_on and self._armed:
            self._armed = False
            raise RuntimeError("模拟中断")
        return self._script.for_role(role)


class _SlowRisk:
    """剧本模型：风控角色的每次回答先在事件循环上等一小段，其余角色照常即刻回答。"""

    def __init__(self, replies: dict, delay: float):
        self._script, self._delay = Script(replies), delay
        self.in_risk = 0                 # 当前有几份风控回答在等

    def for_role(self, role: Role) -> Model:
        fast = self._script.for_role(role)
        if not role.name.startswith("risk."):
            return fast
        assert isinstance(fast, FunctionModel) and fast.function is not None
        port = self

        async def respond(messages: list, info: Any) -> Any:
            port.in_risk += 1
            try:
                await asyncio.sleep(port._delay)
                return fast.function(messages, info)   # type: ignore[misc]
            finally:
                port.in_risk -= 1

        return FunctionModel(respond, model_name="slow-scripted")


class _LateFailure:
    """剧本模型：fail_on 角色的回答在短暂延迟后以 ModelAPIError 拒绝（其余角色照常）。
    延迟保证并行的兄弟分支先算完，缺陷才看得见。"""

    def __init__(self, replies: dict, fail_on: str, delay: float = 0.05):
        self._script, self._fail_on, self._delay = Script(replies), fail_on, delay

    def for_role(self, role: Role) -> Model:
        if role.name != self._fail_on:
            return self._script.for_role(role)

        async def respond(messages: list, info: Any) -> Any:
            await asyncio.sleep(self._delay)
            raise ModelAPIError("scripted", "第二位分析师的模型不可用（测试注入）")

        return FunctionModel(respond, model_name="scripted")


# ---- D1（20.7 第 1 条）：辩论期间事件循环保持可用 ----


async def test_the_event_loop_stays_available_while_the_debate_runs():
    """风险辩论跑在慢速剧本模型下时，同一循环上的另一个协程能持续推进。
    同步线程桥会把主循环阻塞整段辩论：心跳在风控等待期间一次也走不动。"""
    scenario = load_one("risk_shrinks_size")
    port = _SlowRisk(scenario.replies, delay=0.06)
    run = _run(port)
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.005)
            if port.in_risk > 0:            # 只数风控调用在等的窗口
                ticks += 1

    heart = asyncio.create_task(heartbeat())
    try:
        outcome = await analyze(run, dict(scenario.request), _deps(), scenario.config.flow)
    finally:
        stop.set()
        await heart
    assert outcome.status == "delivered"    # 辩论照常跑完
    assert _actors(run)[-6:-2] == ["risk.aggressive", "risk.conservative", "risk.neutral", "trader"]
    assert ticks >= 10, f"辩论期间事件循环被阻塞：风控等待窗口内心跳只走了 {ticks} 次"


# ---- D3 流程这一半（20.7 第 3 条）：带工具的运行中断后可续跑 ----


async def test_a_tools_run_interrupted_after_manager_resumes_and_delivers(tmp_path):
    """带工具的运行在 manager 之后崩溃，按 app/resume.py 的方式从日志重建 Run 再续跑：
    intake 不重跑，run.tools 必须在续跑入口重建；审计打回技术分析师时才见分晓（内核取
    工具处 KeyError）。板上已有的工具事实装回工具盒，新调用的事实接在旧事实之后。"""
    from insightcrew.app.resume import _budget, _ResumeJournal, _ResumingBoard, _ResumingRun, _steps_used
    from insightcrew.app.wiring import checkpoint

    scenario = load_one("tools_clean")
    replies = dict(scenario.replies)
    replies["technical"] = [
        {"tool": "indicator", "args": {"name": "sma", "n": 30}},      # 第一轮：先调一次工具
        {"claims": [{"fact": "close", "value": 230.85, "reading": "收盘价站上三条均线"},
                    {"fact": "ghost_99", "value": 1.0, "reading": "不存在的事实"}],   # 审计将打回
         "narrative": "初版：引用了一个不存在的事实。"},
        {"tool": "indicator", "args": {"name": "sma", "n": 50}},      # 第二轮：换一个窗口再调
        replies["technical"][-1],                                     # 干净的最终分析（场景原文）
    ]
    cfg = scenario.config.flow
    deps = _deps()

    path = tmp_path / "journal.jsonl"
    crashed_journal = Journal(path)
    crashed = Run("t", crashed_journal, Board(crashed_journal), _CrashOnce(replies, "trader"), Budget())
    with pytest.raises(RuntimeError):
        async with checkpoint(tmp_path) as saver:
            await drive(crashed, dict(scenario.request), deps, cfg, checkpointer=saver,
                        thread_id="tools-resume")
    events = Journal.load(path)
    assert [e.actor for e in events if e.type == "model.called"] == \
        ["technical", "bull", "bear", "manager"]                      # 恰在 manager 之后断

    journal = _ResumeJournal(path)                                    # 从日志重建（app/resume 的路）
    resumed = _ResumingRun(id="t", journal=journal, board=_ResumingBoard(journal, events),
                           models=Script({**replies, "technical": replies["technical"][2:]}),
                           budget=_budget({"ceilings": {}}, events), steps=_steps_used(events))
    async with checkpoint(tmp_path) as saver:
        outcome = await drive(resumed, dict(scenario.request), deps, cfg, checkpointer=saver,
                              thread_id="tools-resume", resume=True)
    assert outcome.status == "delivered"
    actors = _actors(resumed)
    assert actors.count("technical") == 2                             # 打回后重跑了一次
    versions = resumed.board.history("tools/facts")
    assert [f["id"] for f in versions[-1]] == ["sma_30", "sma_50"]    # 旧事实在，新事实接在后面
    assert resumed.board.get("run/outcome")["status"] == "delivered"


# ---- 20.7 第 4 条：兄弟分支的花费 ----


async def test_a_halting_branch_still_journals_its_siblings_spend(monkeypatch):
    """并行扇出里第二位分析师的模型不可用而停机：第一位分析师已算完的调用照样入账
    （model.called 在日志里，token 计入 run/usage），停机原因仍是 model_unavailable。"""
    from insightcrew.flows import analyze as flow

    from . import fake_stages

    monkeypatch.setattr(flow, "stages", fake_stages)

    def _available(run: Run, deps: Any, channels: tuple[str, ...]) -> None:
        for channel in channels:
            run.board.put(f"{channel}/status",
                          {"channel": channel, "state": "available", "reason": ""}, by="evidence")
        run.journal.append("gate.checked", actor="evidence", data={"gate": "evidence", "findings": 0})

    monkeypatch.setattr(fake_stages, "evidence", _available)

    run = _run(_LateFailure({**_BASE_REPLIES, "fundamentals": [_FUNDAMENTALS]}, "fundamentals"))
    outcome = await analyze(run, dict(REQUEST), None, _CHANNELS_FLOW)
    assert outcome.status == "review" and outcome.reason == "model_unavailable"
    failed = [e.actor for e in run.journal.events if e.type == "model.failed"]
    assert failed == ["fundamentals"]                                 # 停机的是第二位分析师
    assert _actors(run) == ["technical"]                              # 兄弟的调用在日志里
    usage = run.board.get("run/usage")
    technical = [row for row in usage if row["role"] == "technical"]
    assert technical and technical[0]["input_tokens"] + technical[0]["output_tokens"] == 1300
