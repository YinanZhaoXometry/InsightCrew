"""剧本与回放两个离线模型来源的测试（WP33；剧本学工具调用，BLUEPRINT 19.11）。

剧本的三种回答各走各的路：普通 JSON 对象经输出工具变成契约输出；纯文字原样返回；
恰有两个键 {"tool", "args"} 的对象是一次工具调用——内核的工具包装真的执行它，
结果送回模型，剧本再按次序给最终输出。回放则把录制时的工具调用回复原样重放，
工具再次执行、必须又给出同一个值。
"""
import json

from pydantic import BaseModel, ConfigDict

from insightcrew.adapters.scripted import Replay, Script
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Halt, Malformed, Role, Run, run_role


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Note(Contract):
    action: str
    cites: tuple[str, ...]


TRADER = Role("trader", "quick", "你是交易员。", ("ledger/*",), "trader/note", Note)
TOOL_ROLE = Role("technical", "quick", "你是技术分析师。", ("ledger/*",), "technical/note",
                 Note, tools=("indicator",), max_tool_calls=2)
GOOD = {"action": "Buy", "cites": ["close", "atr_14"]}
EXPECTED = Note.model_validate(GOOD)                  # 契约会把列表收敛成元组


def make_run(models, path=None, **kw) -> Run:
    journal = Journal() if path is None else Journal(path / "journal.jsonl")
    return Run("t", journal, Board(journal), models, Budget(), **kw)


def seed(run: Run) -> None:
    run.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")


def called(run: Run):
    return next(e for e in run.journal.events if e.type == "model.called")


def _indicator(calls):
    def indicator(name: str, n: int) -> dict:
        """按名字与窗口取一个指标值。"""
        calls.append((name, n))
        return {"id": f"{name}_{n}", "value": n * 1.5, "unit": "USD", "as_of": "2026-09-30"}

    return indicator


# ---- 剧本的基本回答：与 2.2 之前完全一致 ----

async def test_a_plain_object_reply_becomes_the_contract_output(tmp_path):
    run = make_run(Script({"trader": [GOOD]}), tmp_path)
    seed(run)
    assert (await run_role(run, TRADER)) == EXPECTED


async def test_a_text_reply_is_returned_verbatim_and_repeats_after_exhaustion(tmp_path):
    """纯文字回答原样返回；用完后重复最后一条（纠正重试两次都失败 -> Malformed）。"""
    run = make_run(Script({"trader": ["not json at all"]}), tmp_path)
    seed(run)
    try:
        await run_role(run, TRADER)
        raise AssertionError("两次都不合契约，应当 Malformed")
    except Malformed:
        pass
    texts = [p["content"] for m in called(run).data["messages"] if m["kind"] == "response"
             for p in m["parts"] if p["part_kind"] == "text"]
    assert texts == ["not json at all", "not json at all"]   # 第二次请求重复了最后一条


async def test_a_role_without_replies_fails_the_call(tmp_path):
    run = make_run(Script({}), tmp_path)
    seed(run)
    try:
        await run_role(run, TRADER)
        raise AssertionError("剧本里没有回答，应当以 model_unavailable 停下")
    except Halt as halt:
        assert halt.reason == "model_unavailable"


# ---- 剧本里的工具调用（19.11 的回答形状） ----

async def test_a_scripted_tool_call_runs_the_real_tool_before_the_final_output(tmp_path):
    calls: list[tuple[str, int]] = []
    replies = [{"tool": "indicator", "args": {"name": "sma", "n": 30}}, GOOD]
    run = make_run(Script({"technical": replies}), tmp_path,
                   tools={"indicator": _indicator(calls)})
    seed(run)
    note = await run_role(run, TOOL_ROLE)
    assert note == EXPECTED                               # 最终输出照常从队尾给出
    assert calls == [("sma", 30)]                         # 工具真的执行了一次
    event = called(run)
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (1, 0)
    assert event.data["usage"]["requests"] == 2           # 一次工具调用 + 一次最终输出
    sent = json.dumps(event.data["messages"], ensure_ascii=False)
    assert "45.0" in sent                                 # 工具结果（30 * 1.5）送回了模型
    assert "sma_30" in sent


async def test_a_scripted_tool_call_repeats_are_served_from_the_kernel_cache(tmp_path):
    calls: list[tuple[str, int]] = []
    tool_call = {"tool": "indicator", "args": {"name": "sma", "n": 30}}
    replies = [tool_call, tool_call, GOOD]                # 同名同参两次：第二次回放缓存
    run = make_run(Script({"technical": replies}), tmp_path,
                   tools={"indicator": _indicator(calls)})
    seed(run)
    await run_role(run, TOOL_ROLE)
    assert calls == [("sma", 30)]                         # 只真正执行了一次
    assert called(run).data["repeated_tool_calls"] == 1


async def test_only_the_exact_two_keys_make_a_tool_call(tmp_path):
    """多一个键就不是工具调用，而是交给输出工具的最终输出（不合契约 -> Malformed）。"""
    run = make_run(Script({"technical": [{"tool": "indicator", "args": {}, "why": "试一下"}]}),
                   tmp_path, tools={"indicator": _indicator([])})
    seed(run)
    try:
        await run_role(run, TOOL_ROLE)
        raise AssertionError("该回答应按输出契约处理，两次校验都不合契约")
    except Malformed:
        pass
    assert called(run).data["tool_calls"] == 0            # 工具一次也没执行


# ---- 回放：录制时的工具调用原样重放，工具再次执行 ----

async def test_replay_replays_a_recorded_tool_call_and_reruns_the_tool(tmp_path):
    calls: list[tuple[str, int]] = []
    replies = [{"tool": "indicator", "args": {"name": "sma", "n": 30}}, GOOD]
    run1 = make_run(Script({"technical": replies}), tmp_path / "one",
                    tools={"indicator": _indicator(calls)})
    seed(run1)
    await run_role(run1, TOOL_ROLE)
    assert calls == [("sma", 30)]

    run2 = make_run(Replay(run1.journal.events), tmp_path / "two",
                    tools={"indicator": _indicator(calls)})
    seed(run2)
    note = await run_role(run2, TOOL_ROLE)
    assert note == EXPECTED                              # 回放给出同一份输出
    assert calls == [("sma", 30), ("sma", 30)]            # 工具在重放里又执行了一次
    event = called(run2)
    assert (event.data["tool_calls"], event.data["repeated_tool_calls"]) == (1, 0)
    assert run2.board.snapshot() == run1.board.snapshot()
