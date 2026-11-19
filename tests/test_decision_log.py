"""决策日志的测试：幂等的 decision、resolve 只补 pending、max_entries 只淘汰已结算对
且绝不删 pending、坏行拒绝，以及两个进程同时追加时谁都不丢（BLUEPRINT 19.13）。"""
from __future__ import annotations

import multiprocessing
import re
from pathlib import Path

import pytest

from insightcrew.adapters.decision_log import DecisionLog
from insightcrew.domain.memory import DecisionRecord, OutcomeRecord
from insightcrew.domain.types import Action, Rating


def decision(as_of: str, ticker: str, run_id: str, *,
             action: Action = Action.BUY) -> DecisionRecord:
    return DecisionRecord(kind="decision", run_id=run_id, ticker=ticker, as_of=as_of,
                          rating=Rating.BUY, action=action, entry=100.0, stop=95.0,
                          target=120.0, size_pct=10.0, horizon_days=5)


def outcome(dec: DecisionRecord, known_on: str = "2026-01-13",
            lesson: str | None = "教训") -> OutcomeRecord:
    return OutcomeRecord(kind="outcome", run_id=dec.run_id, ticker=dec.ticker,
                         as_of=dec.as_of, known_on=known_on, holding_days=5,
                         return_pct=1.0, benchmark="SPY", benchmark_return_pct=0.5,
                         alpha_pct=0.5, hit="neither", lesson=lesson)


def test_missing_file_reads_empty(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.records() == []
    assert log.pending() == []


def test_append_roundtrips_and_ignores_a_duplicate_decision(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    first = decision("2026-01-05", "ACME", "run-1")
    assert log.append(first) is True
    assert log.records() == [first]
    again = decision("2026-01-05", "ACME", "run-2")          # 同一 (as_of, ticker)
    assert log.append(again) is False
    assert log.records() == [first]
    other_day = decision("2026-01-06", "ACME", "run-3")      # 同代码不同日：不算重复
    assert log.append(other_day) is True
    assert log.records() == [first, other_day]


def test_resolve_only_fills_a_pending_decision_and_is_idempotent(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    dec = decision("2026-01-05", "ACME", "run-1")
    log.append(dec)
    assert [d.run_id for d in log.pending()] == ["run-1"]
    assert log.resolve(outcome(dec)) is True
    assert log.pending() == []
    assert log.records() == [dec, outcome(dec)]
    assert log.resolve(outcome(dec, lesson="第二遍")) is False    # 同一决策只结一次
    assert log.records() == [dec, outcome(dec)]
    stranger = decision("2026-01-07", "ACME", "run-9")
    assert log.resolve(outcome(stranger)) is False               # 没有决策可配对
    assert len(log.records()) == 2


def test_resolve_keeps_the_written_lesson(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    dec = decision("2026-01-05", "ACME", "run-1")
    log.append(dec)
    log.resolve(outcome(dec, lesson="见坏消息先查持仓"))
    stored = next(r for r in log.records() if isinstance(r, OutcomeRecord))
    assert stored.lesson == "见坏消息先查持仓"


def test_max_entries_drops_oldest_settled_pairs_and_never_a_pending_one(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl", max_entries=1)
    d1 = decision("2026-01-05", "ACME", "run-1")
    d2 = decision("2026-01-06", "0700.HK", "run-2")
    d3 = decision("2026-01-07", "BRKA", "run-3")
    for d in (d1, d2, d3):
        log.append(d)
    log.resolve(outcome(d1))
    assert len(log.records()) == 4                              # 一对结算，其余 pending
    log.resolve(outcome(d2))                                    # 超过 1 对：删最旧的整对
    assert log.records() == [d2, d3, outcome(d2)]               # 结果追加在文件末尾
    assert [d.run_id for d in log.pending()] == ["run-3"]        # pending 的永不删
    log.resolve(outcome(d3))
    assert log.records() == [d3, outcome(d3)]
    assert log.pending() == []


def test_max_entries_zero_means_no_limit(tmp_path: Path):
    log = DecisionLog(tmp_path / "decisions.jsonl", max_entries=0)
    for day in range(1, 6):
        dec = decision(f"2026-01-0{day}", f"T{day}", f"run-{day}")
        log.append(dec)
        log.resolve(outcome(dec))
    assert len(log.records()) == 10


def test_a_broken_line_is_refused_with_the_file_named(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    path.write_text('{"kind": "decision"}\n', encoding="utf-8")   # 缺必填字段
    with pytest.raises(ValueError, match=re.escape("decisions.jsonl")):
        DecisionLog(path).records()
    path.write_text("不是 JSON\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 1 行"):
        DecisionLog(path).records()
    path.write_text('{"kind": "其他"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="不是决策或结果记录"):
        DecisionLog(path).records()


# ---- 两个进程同时追加：谁都不丢（改写自 TradingAgents 的文件锁场景）


def _child_append(path: str, ticker: str, days: tuple[str, ...]) -> None:
    """子进程：往同一个日志里连着追加几条决策。必须是模块级函数（spawn 可序列化）。"""
    log = DecisionLog(path)
    for i, day in enumerate(days):
        log.append(decision(day, ticker, f"run-{ticker}-{i}"))


def test_two_processes_appending_at_once_lose_nothing(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    days = ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")
    ctx = multiprocessing.get_context("spawn")
    children = [ctx.Process(target=_child_append, args=(str(path), ticker, days))
                for ticker in ("ACME", "0700.HK")]
    for child in children:
        child.start()
    for child in children:
        child.join(60)
    assert all(child.exitcode == 0 for child in children)
    records = DecisionLog(path).records()
    keys = {(r.as_of, r.ticker) for r in records}               # 8 条决策一条不丢、不重
    assert keys == {(day, ticker) for ticker in ("ACME", "0700.HK") for day in days}
    assert len(records) == 8
    assert len(DecisionLog(path).pending()) == 8
