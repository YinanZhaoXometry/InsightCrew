"""内核 2.3 的验收测试（BLUEPRINT 20.5，WP40）：观察者钩子只许测量，不许改变运行。

四件事：
    1. 无观察者时，事件与 2.2 逐条一致——钩子不写日志、不加字段，也不多走一次时钟
       （tests/test_kernel.py:142 的计数时钟正是为此）；
    2. 带记录观察者时，role.started 与 role.finished 依序到达，字段恰为 20.5 的表格
       （带工具的角色 tool_calls 记实际执行次数）；
    3. 观察者抛异常，日志与无观察者的运行完全相等（不变量 23）；
    4. 模型故障在 Halt 之前通知 role.failed。

messages 里带有每次执行都会变的时间戳与运行/调用 ID（2.2 之前就是这样），
逐条比较事件时把它们剥掉。
"""
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from insightcrew.adapters.scripted import Script
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Event, Journal
from insightcrew.kernel.runtime import Halt, Role, Run, run_role


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Note(Contract):
    action: str
    entry: float
    cites: tuple[str, ...]


GOOD = {"action": "Buy", "entry": 189.5, "cites": ["close", "atr_14"]}

TRADER = Role("trader", "quick", "你是交易员。", ("ledger/*",), "trader/note", Note)
TOOL_ROLE = Role("technical", "quick", "你是技术分析师。", ("ledger/*",), "technical/note",
                 Note, tools=("indicator",), max_tool_calls=2)
TOOL_REPLY = {"tool": "indicator", "args": {"name": "sma", "window": 20}}


def make_run(replies, path: Path, **kw) -> Run:
    journal = Journal(path / "journal.jsonl")
    return Run("r1", journal, Board(journal), Script(replies), Budget(), **kw)


def seed(run: Run) -> None:
    run.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")


def called(run: Run) -> list[Event]:
    return [e for e in run.journal.events if e.type == "model.called"]


def recorder(events: list[tuple[str, dict[str, Any]]]) -> Callable[[str, Mapping[str, Any]], None]:
    """把每次通知的种类与数据记进列表的观察者。"""

    def observe(kind: str, data: Mapping[str, Any]) -> None:
        events.append((kind, dict(data)))

    return observe


def _indicator(calls: list[tuple[str, int]]):
    def indicator(name: str, window: int) -> float:
        """按名字与窗口取一个指标值。"""
        calls.append((name, window))
        return window * 1.5

    return indicator


def counting_clock(ticks: list[float]) -> Callable[[], float]:
    """每走一次记一笔的时钟；返回值恒为 0，时长闸门永不触发。"""

    def clock() -> float:
        ticks.append(0.0)
        return 0.0

    return clock


_VOLATILE = frozenset({"timestamp", "run_id", "conversation_id", "tool_call_id"})


def _plain(events) -> list:
    """事件的可比形式：剥掉 messages 里每次执行都会变的字段，其余原样保留。"""

    def scrub(value):
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if k not in _VOLATILE}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return [scrub(e.model_dump()) for e in events]


# ---- 第 1 项验收：无观察者，行为与 2.2 完全一致 ----

async def test_with_no_observer_the_events_are_identical_to_before(tmp_path):
    silent_ticks: list[float] = []
    seen_ticks: list[float] = []
    events: list[tuple[str, dict[str, Any]]] = []
    silent = make_run({"trader": [GOOD]}, tmp_path / "silent", clock=counting_clock(silent_ticks))
    seen = make_run({"trader": [GOOD]}, tmp_path / "seen",
                    clock=counting_clock(seen_ticks), observe=recorder(events))
    for run in (silent, seen):
        seed(run)

    await run_role(silent, TRADER)
    await run_role(seen, TRADER)
    assert _plain(seen.journal.events) == _plain(silent.journal.events)   # 钩子不留痕迹
    assert seen.board.snapshot() == silent.board.snapshot()
    assert len(silent_ticks) == 2        # 与 2.2 相同：admit 的两次（首次记起点、查时长），一次不多
    assert len(seen_ticks) == 4          # 有观察者才多走两次：began 与收尾的 ms
    assert set(called(silent)[0].data) == {"model", "prompt", "messages", "output", "usage", "cost"}


# ---- 第 2 项验收：记录观察者看到的三种通知与字段 ----

async def test_a_recording_observer_sees_started_then_finished_with_the_fields(tmp_path):
    events: list[tuple[str, dict[str, Any]]] = []
    run = make_run({"trader": [GOOD]}, tmp_path, observe=recorder(events))
    seed(run)
    await run_role(run, TRADER)

    assert [kind for kind, _ in events] == ["role.started", "role.finished"]
    started, finished = (data for _, data in events)
    assert started == {"step": "trader#1", "role": "trader", "tier": "quick", "model": "scripted"}
    assert finished["step"] == "trader#1" and finished["role"] == "trader"
    assert set(finished) == {"step", "role", "ms", "ok", "requests",
                             "input_tokens", "output_tokens", "tool_calls"}
    assert finished["ok"] is True
    assert (finished["requests"], finished["input_tokens"], finished["output_tokens"]) == (1, 1000, 300)
    assert finished["tool_calls"] == 0                    # 无工具的角色记 0
    assert isinstance(finished["ms"], int) and finished["ms"] >= 0


async def test_a_tool_role_reports_the_calls_it_actually_ran(tmp_path):
    calls: list[tuple[str, int]] = []
    events: list[tuple[str, dict[str, Any]]] = []
    run = make_run({"technical": [TOOL_REPLY, GOOD]}, tmp_path,
                   tools={"indicator": _indicator(calls)}, observe=recorder(events))
    seed(run)
    await run_role(run, TOOL_ROLE)

    assert calls == [("sma", 20)]                         # 工具真的执行了一次
    finished = events[-1][1]
    assert events[-1][0] == "role.finished"
    assert finished["tool_calls"] == 1                    # ledger.calls，不是 0
    assert finished["requests"] == 2                      # 一次工具调用 + 一次最终输出


# ---- 第 3 项验收：观察者抛异常不影响运行（不变量 23） ----

async def test_an_observer_that_raises_changes_nothing(tmp_path):
    def boom(kind: str, data: Mapping[str, Any]) -> None:
        raise RuntimeError("观察者绝不能影响运行")

    plain, broken = make_run({"trader": [GOOD]}, tmp_path / "plain"), make_run({"trader": [GOOD]},
                                                                               tmp_path / "broken")
    for run in (plain, broken):
        seed(run)
    broken.observe = boom

    out_plain = await run_role(plain, TRADER)
    out_broken = await run_role(broken, TRADER)           # 异常被吞掉，照常交付
    assert out_broken == out_plain
    assert _plain(broken.journal.events) == _plain(plain.journal.events)
    assert broken.board.snapshot() == plain.board.snapshot()


# ---- 第 4 项验收：模型故障通知 role.failed ----

async def test_a_failed_model_call_notifies_role_failed_before_the_halt(tmp_path):
    events: list[tuple[str, dict[str, Any]]] = []
    run = make_run({}, tmp_path, observe=recorder(events))   # 剧本里没有回答 → 模型故障
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(run, TRADER)

    assert [kind for kind, _ in events] == ["role.started", "role.failed"]
    assert set(events[1][1]) == {"step", "role", "ms"}
    assert events[1][1]["step"] == "trader#1" and events[1][1]["role"] == "trader"
    assert [e.type for e in run.journal.events] == ["model.failed"]   # 日志照旧，只有 model.failed
