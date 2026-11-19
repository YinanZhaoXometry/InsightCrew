"""内核 2.2 的验收测试（BLUEPRINT 19.18 WP23）。

覆盖四件事：
    1. run_role 拆成 run_role_deferred + commit 前后，事件逐条一致（含纠正重试、
       Malformed 与模型故障三种情形）；
    2. 先 run_role_deferred 后 commit，结果与 run_role 完全相等；
    3. 两次算完的角色按 commit 的顺序（而不是算完的顺序）入账；
    4. 带工具的角色经 Pydantic AI 的 FunctionModel 调用工具：上限后不再执行、
       同名同参的重复调用回放缓存并计数、请求数封顶在 max_tool_calls + 2。

messages 里带有每次执行都会变的时间戳与运行/调用 ID（2.2 之前就是这样，
回放只比对提示词，所以无碍），逐条比较事件时把它们剥掉。
"""
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from insightcrew.adapters.scripted import Script
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Event, Journal
from insightcrew.kernel.runtime import (
    Deferred,
    Halt,
    Malformed,
    Role,
    Run,
    commit,
    run_role,
    run_role_deferred,
)


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Note(Contract):
    action: str
    entry: float
    cites: tuple[str, ...]


GOOD = {"action": "Buy", "entry": 189.5, "cites": ["close", "atr_14"]}

TRADER = Role("trader", "quick", "你是交易员。", ("ledger/*",), "trader/note", Note)
MANAGER = Role("manager", "deep", "你是主管。", ("ledger/*",), "manager/note", Note)
TOOL_ROLE = Role("technical", "quick", "你是技术分析师。", ("ledger/*",), "technical/note",
                 Note, tools=("indicator",), max_tool_calls=2)
TOOL_CALL = ("indicator", {"name": "sma", "window": 20})


def make_run(replies, path: Path, *, models=None, **kw) -> Run:
    journal = Journal(path / "journal.jsonl")
    return Run("r1", journal, Board(journal), models if models is not None else Script(replies),
               Budget(), **kw)


def seed(run: Run) -> None:
    run.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")


def called(run: Run) -> list[Event]:
    return [e for e in run.journal.events if e.type == "model.called"]


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


class ToolScript:
    """按次序出队的剧本模型（Pydantic AI 的 FunctionModel）。

    回答是 (工具名, 参数) 元组时表示一次工具调用；是字典时表示最终输出。
    """

    def __init__(self, replies, *, name: str = "toolscripted"):
        self._replies, self._name = replies, name
        self._usage = RequestUsage(input_tokens=1000, output_tokens=300)
        self._count = 0

    def for_role(self, role: Role) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            i = self._count
            self._count = i + 1
            reply = self._replies[min(i, len(self._replies) - 1)]
            if isinstance(reply, tuple):
                part: TextPart | ToolCallPart = ToolCallPart(reply[0], reply[1])
            elif info.output_tools:
                part = ToolCallPart(info.output_tools[0].name, reply)
            else:
                part = TextPart(reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))
            return ModelResponse(parts=[part], usage=self._usage, model_name=self._name)

        return FunctionModel(respond, model_name=self._name)


def _indicator(calls: list[tuple[str, int]]):
    def indicator(name: str, window: int) -> float:
        """按名字与窗口取一个指标值。"""
        calls.append((name, window))
        return window * 1.5

    return indicator


async def _both_ways(replies, root: Path) -> tuple[Run, Run]:
    """同一种剧本各备一次运行：一边走 run_role，一边走 deferred + commit。"""
    straight = make_run(replies, root / "straight")
    deferred = make_run(replies, root / "deferred")
    for run in (straight, deferred):
        seed(run)
    return straight, deferred


# ---- 第 1 与第 2 项验收：拆分不改行为，deferred + commit 等于 run_role ----

async def test_run_role_events_are_identical_before_and_after_the_split(tmp_path):
    for replies in ({"trader": [GOOD]},                             # 一次成功
                    {"trader": ['{"entry": 1}', GOOD]}):            # 一次纠正重试后成功
        straight, deferred = await _both_ways(replies, tmp_path)
        await run_role(straight, TRADER)
        commit(deferred, await run_role_deferred(deferred, TRADER))
        assert _plain(deferred.journal.events) == _plain(straight.journal.events)
        assert deferred.board.snapshot() == straight.board.snapshot()


async def test_deferred_then_commit_equals_run_role(tmp_path):
    straight, deferred = await _both_ways({"trader": ['{"entry": 1}', GOOD]}, tmp_path)
    out_straight = await run_role(straight, TRADER)
    out_deferred = commit(deferred, await run_role_deferred(deferred, TRADER))
    assert out_deferred == out_straight                            # 同一份契约输出
    assert deferred.budget.table() == straight.budget.table()
    assert deferred.budget.tokens == straight.budget.tokens
    assert deferred.journal.events[-1].type == "artifact.written"
    assert Board.fold(deferred.journal.events).snapshot() == deferred.board.snapshot()


async def test_the_malformed_path_is_identical_too(tmp_path):
    straight, deferred = await _both_ways({"trader": ["not json at all"]}, tmp_path)
    with pytest.raises(Malformed):
        await run_role(straight, TRADER)
    work = await run_role_deferred(deferred, TRADER)                # 算完了，但入账才知道失败
    assert work.output is None and work.failure is not None
    with pytest.raises(Malformed):
        commit(deferred, work)
    assert _plain(deferred.journal.events) == _plain(straight.journal.events)
    assert not deferred.board.has("trader/note")                    # 失败的调用记账留痕，不上黑板


async def test_a_failing_model_halts_identically(tmp_path):
    straight, deferred = await _both_ways({}, tmp_path)
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(straight, TRADER)
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role_deferred(deferred, TRADER)                   # 这条路不产出 Deferred，直接停
    assert deferred.journal.events == straight.journal.events


# ---- 第 3 项验收：入账顺序即 commit 顺序 ----

async def test_two_deferred_calls_commit_in_commit_order_not_completion_order(tmp_path):
    run = make_run({"trader": [GOOD], "manager": [GOOD]}, tmp_path)
    seed(run)                                                       # 日志里此刻只有 seq 1 的种子
    first = await run_role_deferred(run, TRADER)                    # 先算完的是 trader
    second = await run_role_deferred(run, MANAGER)                  # 后算完的是 manager
    assert (first.step, second.step) == ("trader#1", "manager#1")
    assert [e.type for e in run.journal.events if e.type != "artifact.written"] == []
    assert not run.board.has("trader/note") and not run.board.has("manager/note")

    commit(run, second)                                             # 后算完的先入账
    commit(run, first)
    assert [(e.actor, e.type) for e in run.journal.events] == [
        ("ledger", "artifact.written"),
        ("manager", "model.called"), ("manager", "artifact.written"),
        ("trader", "model.called"), ("trader", "artifact.written")]
    assert run.journal.events[1].caused_by == (1,)                  # 视图来自算的时候，不来自入账的时候
    assert run.journal.events[4].caused_by == (4,)                  # 产出引用自己的 model.called
    assert Board.fold(run.journal.events).snapshot() == run.board.snapshot()


# ---- 第 4 项验收：工具 ----

async def test_a_tool_is_called_through_a_function_model_and_repeats_are_cached(tmp_path):
    calls: list[tuple[str, int]] = []
    replies = [TOOL_CALL, TOOL_CALL, GOOD]                          # 第二次同名同参，不该重新执行
    run = make_run(replies, tmp_path, models=ToolScript(replies),
                   tools={"indicator": _indicator(calls)})
    note = await run_role(run, TOOL_ROLE)
    assert note.entry == 189.5
    assert calls == [("sma", 20)]                                   # 只真正执行了一次
    event = called(run)[0]
    assert event.data["model"] == "toolscripted"
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (1, 1)
    assert event.data["usage"]["requests"] == 3
    sent = json.dumps(event.data["messages"], ensure_ascii=False)
    assert "30.0" in sent                                           # 首次与重复那次的结果都在消息里
    assert "已达到工具调用上限" not in sent


async def test_after_the_cap_every_further_call_returns_the_message_without_running(tmp_path):
    calls: list[tuple[str, int]] = []
    replies = [("indicator", {"name": "sma", "window": 20}),
               ("indicator", {"name": "sma", "window": 30}),
               ("indicator", {"name": "sma", "window": 40}),        # 上限 2 次已用完
               GOOD]
    run = make_run(replies, tmp_path, models=ToolScript(replies),
                   tools={"indicator": _indicator(calls)})
    note = await run_role(run, TOOL_ROLE)
    assert calls == [("sma", 20), ("sma", 30)]                      # 第三次没有执行
    event = called(run)[0]
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (2, 0)
    assert event.data["usage"]["requests"] == 4                     # 恰好是 max_tool_calls + 2
    assert json.dumps(event.data["messages"], ensure_ascii=False).count(
        "已达到工具调用上限，请立即给出最终输出。") == 1
    assert note.cites == ("close", "atr_14")


async def test_requests_are_capped_at_max_tool_calls_plus_two(tmp_path):
    calls: list[tuple[str, int]] = []
    role = Role("technical", "quick", "你是技术分析师。", ("ledger/*",), "technical/note",
                Note, tools=("indicator",), max_tool_calls=0)       # 一次工具也不许执行
    replies = [TOOL_CALL, TOOL_CALL]                                # 模型两次都还要工具，不给最终输出
    run = make_run(replies, tmp_path, models=ToolScript(replies),
                   tools={"indicator": _indicator(calls)})
    with pytest.raises(Malformed):
        await run_role(run, role)
    event = called(run)[0]                                          # 失败的调用也记账、也留痕
    assert event.data["output"] is None
    assert event.data["usage"]["requests"] == 2
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (0, 0)
    assert json.dumps(event.data["messages"], ensure_ascii=False).count(
        "已达到工具调用上限，请立即给出最终输出。") == 2
    assert calls == [] and not run.board.has("technical/note")


async def test_a_role_without_tools_has_no_tool_counters_in_the_journal(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path)
    seed(run)
    await run_role(run, TRADER)
    assert "tool_calls" not in called(run)[0].data                  # 无工具角色的 data 与过去逐字相同
    assert "repeated_tool_calls" not in called(run)[0].data


async def test_a_named_tool_must_be_supplied_by_run_tools(tmp_path):
    run = make_run([GOOD], tmp_path, models=ToolScript([GOOD]), tools={})
    with pytest.raises(KeyError):                                   # 内核不认识任何工具名，装配必须给全
        await run_role_deferred(run, TOOL_ROLE)


# ---- 契约本身 ----

def test_the_new_fields_default_to_off():
    role = Role("x", "quick", "指示", (), "x/note", Note)
    assert (role.tools, role.max_tool_calls) == ((), 0)
    journal = Journal()
    assert Run("r", journal, Board(journal), Script({}), Budget()).tools == {}


async def test_deferred_is_frozen(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path)
    seed(run)
    work = await run_role_deferred(run, TRADER)
    assert isinstance(work, Deferred)

    def overwrite(target: Any, value: str) -> None:                # 冻住的 dataclass 应当拒绝改写
        target.step = value

    with pytest.raises(dataclasses.FrozenInstanceError):
        overwrite(work, "trader#9")
