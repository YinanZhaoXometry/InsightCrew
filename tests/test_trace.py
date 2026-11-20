"""app/trace.py 的测试（BLUEPRINT 20.6、不变量 22）。

行形状（ts 毫秒、kind、字段）、mark 与 load；带 tracer 的运行与不带 tracer 的运行
日志相同（不变量 22）；经 wiring.run_flow 的真实运行留下 trace.jsonl，且
role.started 与 role.finished 逐个成对（WP42 验收）。
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from insightcrew.app.settings import load
from insightcrew.app.trace import Tracer, mark
from insightcrew.app.trace import load as load_trace
from insightcrew.app.wiring import ROOT, open_run, run_flow
from insightcrew.evals.scenarios import load_one
from insightcrew.flows.analyze import analyze
from insightcrew.kernel.events import Event

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")
# messages 里每次执行都会变的字段（与 tests/test_kernel_v22.py 的 _VOLATILE 同一张表）：
# 两次运行的差异只剩它们与 ts，才谈得上"测量不改日志"。
_VOLATILE = frozenset({"timestamp", "run_id", "conversation_id", "tool_call_id"})


def _plain(events: tuple[Event, ...]) -> list[object]:
    def scrub(value: object) -> object:
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if k not in _VOLATILE}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return [scrub(event.model_dump(exclude={"ts"})) for event in events]


# ---- 行形状 ----


def test_tracer_appends_one_line_per_call_with_ts_kind_and_fields(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    tracer = Tracer(path, now=lambda: datetime(2026, 11, 20, 8, 0, 0, 123000, tzinfo=UTC))
    tracer("role.started", {"step": "technical#1", "role": "technical",
                            "tier": "quick", "model": "scripted"})
    tracer("role.finished", {"step": "technical#1", "ms": 12})
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines == [
        {"ts": "2026-11-20T08:00:00.123+00:00", "kind": "role.started", "step": "technical#1",
         "role": "technical", "tier": "quick", "model": "scripted"},
        {"ts": "2026-11-20T08:00:00.123+00:00", "kind": "role.finished",
         "step": "technical#1", "ms": 12}]


def test_mark_writes_the_line_the_app_writes_itself(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    mark(path, "run.resumed", after_seq=17)
    lines = load_trace(path)
    assert len(lines) == 1 and lines[0]["kind"] == "run.resumed" and lines[0]["after_seq"] == 17
    assert _TS.fullmatch(lines[0]["ts"])                 # UTC ISO，毫秒


def test_load_skips_lines_that_do_not_parse_and_returns_empty_for_a_missing_file(
        tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text('{"kind": "a"}\n不是 JSON\n{"kind": "b"}\n', encoding="utf-8")
    assert [line["kind"] for line in load_trace(path)] == ["a", "b"]
    assert load_trace(tmp_path / "missing.jsonl") == []


# ---- 不变量 22 与接线 ----


async def test_a_tracer_changes_no_journal(tmp_path: Path) -> None:
    """不变量 22：测量只是测量。带 tracer 的运行与不带 tracer 的运行，日志除 ts 外逐条相同。"""
    settings = load(ROOT, force_offline=True)
    scenario = load_one("clean")
    journals = []
    for tracer in (None, Tracer(tmp_path / "trace.jsonl")):
        run, deps = open_run(settings, dict(scenario.request), scenario=scenario,
                             flow=scenario.config.flow, runs_dir=tmp_path / "runs")
        if tracer is not None:
            run.observe = tracer
        await analyze(run, dict(scenario.request), deps, scenario.config.flow)
        journals.append(_plain(run.journal.events))
    assert journals[0] == journals[1]


async def test_run_flow_leaves_a_trace_whose_spans_pair_up(tmp_path: Path) -> None:
    """20.6 的接线：经 wiring.run_flow 的运行留下 trace.jsonl；完成的运行里
    role.started 与 role.finished 的 step 序列逐一成对。"""
    settings = load(ROOT, force_offline=True)
    scenario = load_one("clean")
    run, deps = open_run(settings, dict(scenario.request), scenario=scenario,
                         flow=scenario.config.flow, runs_dir=tmp_path)
    await run_flow(run, dict(scenario.request), deps, scenario.config.flow)
    assert deps.out_dir is not None
    trace = load_trace(deps.out_dir / "trace.jsonl")
    assert trace and all(_TS.fullmatch(line["ts"]) and "kind" in line for line in trace)
    assert [line["step"] for line in trace if line["kind"] == "role.started"] == \
           [line["step"] for line in trace if line["kind"] == "role.finished"]
