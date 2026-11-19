"""记忆进流程的测试（BLUEPRINT 19.18 WP35）：课程挑选的不变量 20、交付后的记决策、
两条结算路径核对后的落账与复盘小运行、分歧保持待决、重放替身原样奉还（决策 44）。
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path
from typing import Any, ClassVar

from insightcrew.adapters.decision_log import DecisionLog
from insightcrew.adapters.scripted import Script
from insightcrew.app.settings import load
from insightcrew.app.wiring import ROOT, _ScenarioLog, build_deps
from insightcrew.domain import memory_sql
from insightcrew.domain.memory import DecisionRecord, OutcomeRecord
from insightcrew.domain.types import Action, Rating
from insightcrew.flows.analyze import FlowConfig, analyze
from insightcrew.flows.settle import settle as settle_flow
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Run

_ROOT = ROOT
_SCENARIO = json.loads((Path(__file__).parent.parent / "data" / "scenarios" / "memory_lessons.json")
                       .read_text(encoding="utf-8"))
REPLIES, REQUEST, RECORDS = _SCENARIO["replies"], _SCENARIO["request"], _SCENARIO["config"]["memory"]


def _deps(decision_log: object):
    return dataclasses.replace(build_deps(load(_ROOT)), decision_log=decision_log)


async def _run(cfg: FlowConfig, deps, replies: dict | None = None) -> Run:
    journal = Journal()
    run = Run("t", journal, Board(journal), Script({**REPLIES, **(replies or {})}), Budget())
    outcome = await analyze(run, REQUEST, deps, cfg)
    assert outcome.status == "delivered", outcome.reason
    return run


def _decision(as_of: str = "2026-08-03", *, run_id: str = "seed-1") -> DecisionRecord:
    return DecisionRecord(kind="decision", run_id=run_id, ticker="ACME", as_of=as_of,
                          rating=Rating.BUY, action=Action.BUY, entry=208.85, stop=202.1,
                          target=221.0, size_pct=5.0, horizon_days=20)


# ---- lessons：不变量 20（晚于分析日可知的结果绝不出现）与显式缺席 ----


async def test_lessons_surface_only_outcomes_known_before_the_analysis_date():
    run = await _run(FlowConfig(memory=True), _deps(_ScenarioLog(RECORDS)))
    board = run.board.snapshot()
    lessons = board["memory/lessons"]
    assert board["memory/status"]["state"] == "available"
    assert [entry["as_of"] for entry in lessons] == ["2026-06-01"]     # 只有先前的教训
    assert "放量突破后回踩" in lessons[0]["lesson"]
    prompt = next(e.data["prompt"] for e in run.journal.events
                  if e.type == "model.called" and e.actor == "manager")
    assert "放量突破后回踩" in prompt            # 经理看到了它
    assert "九月缩量上攻" not in prompt          # 分析日之后才可知的那条没有到任何角色


async def test_lessons_none_known_when_nothing_has_settled():
    pending = [row for row in RECORDS if row["kind"] == "decision"][:1]
    run = await _run(FlowConfig(memory=True), _deps(_ScenarioLog(pending)))
    board = run.board.snapshot()
    assert board["memory/lessons"] == []
    assert board["memory/status"]["state"] == "none_known"
    assert board["memory/status"]["reason"]


async def test_lessons_replay_returns_the_journaled_lessons_verbatim():
    class _ReplayLog:
        journaled_lessons: ClassVar[list[dict[str, Any]]] = [
            {"ticker": "ACME", "as_of": "2026-06-01", "known_on": "2026-06-10",
             "rating": "Buy", "return_pct": 1.8, "alpha_pct": None,
             "hit": "target", "lesson": "按日志原样奉还"}]
        journaled_status: ClassVar[dict[str, Any]] = {"state": "available", "reason": ""}

        def records(self):
            return []

        def append(self, record):
            return False               # 重放不写决策日志（wiring._ReplayLog 同形）

    run = await _run(FlowConfig(memory=True), _deps(_ReplayLog()))
    board = run.board.snapshot()
    assert board["memory/lessons"] == _ReplayLog.journaled_lessons   # 决策 44：逐字一致
    assert board["memory/status"] == _ReplayLog.journaled_status


# ---- record：交付记一条，REVIEW 不记 ----


async def test_record_appends_one_decision_for_delivered_and_none_for_review(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    run = await _run(FlowConfig(memory=True), _deps(log))
    records = log.records()
    assert len(records) == 1
    stored = records[0]
    assert isinstance(stored, DecisionRecord)
    assert stored.run_id == run.id and stored.ticker == "ACME" and stored.as_of == "2026-09-30"
    assert stored.rating == "Buy" and stored.action == "Buy"
    assert stored.entry == 230.85 and stored.stop == 225.96826420857275
    assert stored.target == 241.10164516199723 and stored.size_pct == 5.0
    assert stored.horizon_days == 20

    review = Run("r", Journal(), Board(Journal()), Script({}), Budget())    # 没有回答：模型不可用
    outcome = await analyze(review, {**REQUEST, "as_of": "2026-09-29"}, _deps(log),
                            FlowConfig(memory=True))
    assert outcome.status == "review"
    assert len(log.records()) == 1                    # REVIEW 的运行不记决策


# ---- settle：两条路径一致才落账；复盘小运行；分歧保持待决 ----


async def test_settle_resolves_with_a_reflector_run_and_no_benchmark(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.append(_decision())
    models = Script({"reflector": [{"text": "回调不破均线时再进场，宁可错过也不追高",
                                    "cites": ["return_pct", "hit"]}]})
    outcomes = await settle_flow(_deps(log), log, date(2026, 8, 17), holding_days=3,
                                 models=models, runs_root=tmp_path / "settle")
    assert len(outcomes) == 1
    out = outcomes[0]
    assert out.known_on == "2026-08-07"               # 入场 08-04，第 3 根 08-07 离场
    assert out.return_pct == (214.56 - 212.32) / 212.32 * 100
    assert out.benchmark == "SPY" and out.benchmark_return_pct is None and out.alpha_pct is None
    assert out.lesson == "回调不破均线时再进场，宁可错过也不追高"
    assert log.pending() == []                        # 已落账，不再是待决
    runs = list((tmp_path / "settle").iterdir())
    assert len(runs) == 1 and (runs[0] / "journal.jsonl").is_file()
    events = [e.type for e in Journal.load(runs[0] / "journal.jsonl")]
    assert events[0] == "run.started" and "model.called" in events and events[-1] == "run.finished"


async def test_settle_leaves_the_decision_pending_when_the_paths_disagree(
        tmp_path, monkeypatch):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.append(_decision())

    def _tampered(decision, bars, benchmark_bars, holding_days, known_by):
        return memory_sql.SqlOutcome(known_on="1999-01-01", holding_days=holding_days,
                                     return_pct=0.0, benchmark_return_pct=None,
                                     alpha_pct=None, hit="neither")

    monkeypatch.setattr(memory_sql, "settle", _tampered)
    notes: list[str] = []
    outcomes = await settle_flow(_deps(log), log, date(2026, 8, 17), holding_days=3,
                                 models=None, runs_root=None, note=notes.append)
    assert outcomes == []
    assert len(log.pending()) == 1                    # 分歧：保持待决
    assert any("不一致" in line for line in notes)


async def test_settle_without_a_model_stores_the_outcome_with_no_lesson(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.append(_decision())
    outcomes = await settle_flow(_deps(log), log, date(2026, 8, 17), holding_days=3)
    assert len(outcomes) == 1 and outcomes[0].lesson is None
    assert log.pending() == []
    resolved = [r for r in log.records() if isinstance(r, OutcomeRecord)]
    assert len(resolved) == 1 and resolved[0].lesson is None


async def test_settle_skips_decisions_whose_window_has_not_traded(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.append(_decision())
    outcomes = await settle_flow(_deps(log), log, date(2026, 8, 5), holding_days=3)
    assert outcomes == []                             # 窗口只有两根，还没走完
    assert len(log.pending()) == 1
