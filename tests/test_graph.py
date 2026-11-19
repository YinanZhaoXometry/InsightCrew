"""flows/graph.py 的测试（BLUEPRINT 19.5、19.16）。

路径映射遍历：对若干流程配置各跑一次脚本化运行，路由事件里记录的每一个去向都必须是
对应条件边 path_map 的键（router 的返回值就是它）。中断续跑：manager 之后崩溃的运行，
从检查点续跑完，黑板与事件类型序列都与一次未中断的运行相同。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from insightcrew.adapters.scripted import Script
from insightcrew.flows import analyze as flow
from insightcrew.flows.analyze import FlowConfig, analyze, drive
from insightcrew.flows.graph import JUDGE_MAP, audit_map, build_graph
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

from . import fake_stages

CLAIM = {"fact": "rsi_14", "value": 61.3, "reading": "动量偏强"}
ANALYSIS = {"claims": [CLAIM], "narrative": "趋势向上"}
ARG = {"points": [{"text": "动量支持", "cites": ["rsi_14"]}], "rebuttal": ""}
THESIS = {"rating": "Buy", "horizon_days": 20, "points": [{"text": "动量偏强", "cites": ["rsi_14"]}],
          "invalidation": ["跌破 96"], "summary": "买入"}
GOOD = {"action": "Buy", "entry": 100.0, "stop": 96.0, "target": 108.0, "size_pct": 20.0,
        "horizon_days": 20, "cites": ["close", "atr_14"], "rationale": "止损在两倍 ATR 之外"}
RISKY = {**GOOD, "size_pct": 50.0}
BAD_ANALYSIS = {"claims": [{**CLAIM, "value": 31.3}], "narrative": "超卖"}
YES = {"accept": True, "target": "none", "critique": "", "cites": []}
NO_LEVELS = {"accept": False, "target": "levels", "critique": "止损太近", "cites": ["atr_14"]}
NO_THESIS = {"accept": False, "target": "thesis", "critique": "论点不成立", "cites": ["rsi_14"]}

REPLIES = {"technical": [ANALYSIS], "bull": [ARG], "bear": [ARG], "manager": [THESIS],
           "trader": [GOOD], "judge.evidence": [YES], "judge.conservative": [YES],
           "judge.aggressive": [YES]}
REQUEST = {"ticker": "ACME", "as_of": "2026-09-30"}


@pytest.fixture(autouse=True)
def _use_fake_stages(monkeypatch):
    monkeypatch.setattr(flow, "stages", fake_stages)


def _run(replies: dict | None = None, cfg: FlowConfig | None = None) -> Run:
    journal = Journal()
    return Run("t", journal, Board(journal), Script({**REPLIES, **(replies or {})}), Budget())


# ---- 路径映射遍历：每个路由事件的去向都是对应 path_map 的键 ----

_CASES = [
    ("clean", None, None),
    ("audit_to_trader", {"trader": [RISKY, GOOD]}, None),
    ("audit_to_technical", {"technical": [BAD_ANALYSIS, ANALYSIS]}, None),
    ("channels_off", None, FlowConfig()),
    ("channels_both_unavailable", None, FlowConfig(channels=("fundamentals", "disclosures"))),
    ("no_judges", None, FlowConfig(judges=False)),
    ("judge_to_trader", {"trader": [GOOD, {**GOOD, "stop": 95.0, "size_pct": 15.0}],
                         "judge.evidence": [NO_LEVELS, YES],
                         "judge.conservative": [NO_LEVELS, YES]}, None),
    ("judge_to_manager", {"judge.evidence": [NO_THESIS, YES], "judge.conservative": [NO_LEVELS, YES]},
     None),
]


@pytest.mark.parametrize("name,replies,cfg", _CASES, ids=[c[0] for c in _CASES])
async def test_every_route_target_is_a_key_of_its_path_map(name, replies, cfg):
    run = _run(replies, cfg)
    await analyze(run, REQUEST, None, cfg)
    config = cfg or FlowConfig()
    owners = ("technical", *config.channels, "manager", "trader")   # 该配置下在场的责任人
    graph = build_graph(fake_stages, config, None)
    nodes = {n.name for n in graph.get_graph().nodes.values()}
    mapped = audit_map(config, owners, parallel=config.parallel)
    keys = mapped if isinstance(mapped, list) else list(mapped)
    targets = mapped if isinstance(mapped, list) else list(mapped.values())
    for target in set(targets) | set(JUDGE_MAP):
        assert target in nodes, f"path_map 里的 {target} 必须是图里真实存在的节点"
    routed = [e for e in run.journal.events if e.type == "route"]
    for event in routed:
        target = event.data["to"]
        if event.data["why"].startswith("审计"):
            key = target
            if config.parallel and target in ("technical", "fundamentals", "disclosures"):
                key = f"analysts:{target}"             # 并行模式下 router 的返回值带起点
            assert key in keys, f"审计路由到 {key}，不在 path_map {keys}"
        else:
            assert target in JUDGE_MAP, f"评审路由到 {target}，不在 path_map {JUDGE_MAP}"


async def test_shadow_and_stagnation_never_route_to_a_missing_node():
    run = _run({"trader": [RISKY]}, FlowConfig(audit="shadow", judges=False))
    await analyze(run, REQUEST, None, FlowConfig(audit="shadow", judges=False))
    assert not [e for e in run.journal.events if e.type == "route"]


# ---- 并行与串行：同样的日志（不变量 19；除 ts 外逐项相同） ----

async def test_parallel_and_serial_journals_are_equal(monkeypatch):
    """两个通道都可用、三个分析师都真正跑模型：并行与串行的黑板与事件序列相同（ts 除外）。"""
    import json
    from pathlib import Path

    scenario = json.loads((Path(__file__).parent.parent / "data" / "scenarios" /
                           "channels_clean.json").read_text(encoding="utf-8"))

    def _available(run, deps, channels):              # 替身的 evidence：被点名的通道一律记为可用
        for channel in channels:
            run.board.put(f"{channel}/status",
                          {"channel": channel, "state": "available", "reason": ""}, by="evidence")
        run.journal.append("gate.checked", actor="evidence", data={"gate": "evidence", "findings": 0})

    monkeypatch.setattr(fake_stages, "evidence", _available)

    outputs = {}
    for label, cfg in (("parallel", FlowConfig(channels=("fundamentals", "disclosures"))),
                       ("serial", FlowConfig(channels=("fundamentals", "disclosures"), parallel=False))):
        journal = Journal()
        run = Run(label, journal, Board(journal), Script(scenario["replies"]), Budget())
        outputs[label] = (await analyze(run, scenario["request"], None, cfg), run)
    (out_p, run_p), (out_s, run_s) = outputs["parallel"], outputs["serial"]
    assert out_p == out_s
    assert run_p.board.snapshot() == run_s.board.snapshot()
    assert _strip_ts(run_p.journal) == _strip_ts(run_s.journal)
    actors = [e.actor for e in run_p.journal.events if e.type == "model.called"]
    assert actors[:4] == ["technical", "fundamentals", "disclosures", "bull"]   # join 按固定顺序入账


# ---- 中断续跑（决策 42：每个节点在末尾提交，日志与检查点一致） ----

class _CrashOnce:
    """模型端口替身：第一次问到 crash_on 角色时抛普通异常（模拟崩溃，不是 Halt）。"""

    def __init__(self, replies: dict, crash_on: str):
        self._script = Script(replies)
        self._crash_on, self._armed = crash_on, True

    def for_role(self, role):                    # type: ignore[no-untyped-def]
        if role.name == self._crash_on and self._armed:
            self._armed = False
            raise RuntimeError("模拟中断")
        return self._script.for_role(role)


def _strip_ts(journal: Journal) -> list[tuple[int, str, str, str]]:
    return [(e.seq, e.type, e.actor, e.step) for e in journal.events]


async def test_interrupted_after_manager_resumes_to_the_same_board():
    """manager 之后崩溃：续跑完的黑板与事件（类型、角色）序列和一次未中断的运行相同。

    同一 Run 直接续用时，被中断角色的 step_id 已被那次失败的尝试推进（trader#2），
    所以这里只比 (类型, 角色)；从日志重建的续跑（下一个测试）连 step 也一致。
    """
    whole, cfg = _run(), FlowConfig()

    crashed = _run()
    crashed.models = _CrashOnce(REPLIES, "trader")          # type: ignore[assignment]
    saver = InMemorySaver()
    with pytest.raises(RuntimeError):
        await drive(crashed, REQUEST, None, cfg, checkpointer=saver, thread_id="resume-test")
    assert [e.actor for e in crashed.journal.events if e.type == "model.called"] == [
        "technical", "bull", "bear", "manager"]              # 崩溃恰好发生在 trader 之前

    await drive(crashed, REQUEST, None, cfg, checkpointer=saver, thread_id="resume-test",
                resume=True)                                 # 同一 Run：预算与日志续用

    await analyze(whole, REQUEST, None, cfg)
    assert crashed.board.snapshot() == whole.board.snapshot()
    assert [(e.type, e.actor) for e in crashed.journal.events] == \
        [(e.type, e.actor) for e in whole.journal.events]


async def test_resume_from_sqlite_with_rebuilt_journal_and_budget(tmp_path: Path):
    """SQLite 检查点的续跑：日志、黑板、预算、步骤计数按 app/resume.py 的方式重建，
    与未中断运行的差异只剩 ts。"""
    from insightcrew.app.resume import (
        _budget,
        _ResumeJournal,
        _ResumingBoard,
        _ResumingRun,
        _steps_used,
    )
    from insightcrew.app.wiring import checkpoint

    whole, cfg = _run(), FlowConfig()
    await analyze(whole, REQUEST, None, cfg)

    path = tmp_path / "journal.jsonl"
    crash_journal = Journal(path)
    crashed = Run("t", crash_journal, Board(crash_journal), _CrashOnce(REPLIES, "trader"),
                  Budget())
    with pytest.raises(RuntimeError):
        async with checkpoint(tmp_path) as saver:
            await drive(crashed, REQUEST, None, cfg, checkpointer=saver,
                        thread_id="sqlite-resume")
    events = Journal.load(path)

    journal = _ResumeJournal(path)
    resumed = _ResumingRun(id="t", journal=journal, board=_ResumingBoard(journal, events),
                           models=Script(REPLIES), budget=_budget({"ceilings": {}}, events),
                           steps=_steps_used(events))
    async with checkpoint(tmp_path) as saver:
        await drive(resumed, REQUEST, None, cfg, checkpointer=saver,
                    thread_id="sqlite-resume", resume=True)

    assert resumed.board.snapshot() == whole.board.snapshot()
    assert _strip_ts(resumed.journal) == _strip_ts(whole.journal)


def test_resume_refuses_finished_and_foreign_runs(tmp_path: Path):
    """resume 命令的拒绝路径：已结束的运行、没有签名的旧日志、签名不一致的日志。"""
    from insightcrew.app import resume as resume_cmd
    from insightcrew.app.settings import load
    from insightcrew.app.wiring import ROOT

    settings = load(ROOT)
    started = {"request": REQUEST, "models": {"quick": "scripted", "deep": "scripted"},
               "universe": "synthetic"}

    def _write(name: str, data: dict, finished: bool = False) -> Path:
        run_dir = tmp_path / name
        run_dir.mkdir()
        journal = Journal(run_dir / "journal.jsonl")
        journal.append("run.started", actor="app", data=data)
        if finished:
            journal.append("run.finished", actor="flow", data={"status": "delivered", "reason": "ok"})
        return run_dir

    assert resume_cmd.run(settings, str(_write("finished", started, finished=True))) == 1
    assert resume_cmd.run(settings, str(_write("old", started))) == 1          # 没有运行签名
    assert resume_cmd.run(settings, str(_write("foreign", {**started,
                                                          "thread": "deadbeef00000000"}))) == 1
