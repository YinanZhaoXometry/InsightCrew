import enum
import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai.usage import RunUsage

from insightcrew.adapters.scripted import Replay, Script
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget, BudgetConfigError, Ceiling
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Halt, Limits, Malformed, Role, Run, run_role


class Action(enum.StrEnum):
    BUY = "Buy"
    HOLD = "Hold"


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Claim(Contract):
    fact: str = Field(description="事实 ID")
    value: float
    reading: str


class Proposal(Contract):
    action: Action
    entry: float
    stop: float
    target: float | None
    size_pct: float
    horizon_days: int
    cites: tuple[str, ...]
    lens: Literal["a", "b"]
    claims: tuple[Claim, ...]


GOOD = {"action": "Buy", "entry": 189.5, "stop": 181, "target": None, "size_pct": 8.0,
        "horizon_days": 20, "cites": ["close", "atr_14"], "lens": "a",
        "claims": [{"fact": "rsi_14", "value": 61.3, "reading": "ok"}]}

TRADER = Role("trader", "quick", "你是交易员。", ("ledger/*", "manager/thesis"), "trader/proposal", Proposal)


def make_run(replies, tmp_path, **kw) -> Run:
    journal = Journal(tmp_path / "journal.jsonl")
    return Run("r1", journal, Board(journal), Script(replies), Budget(kw.pop("ceiling", None)), **kw)


def called(run: Run):
    return [e for e in run.journal.events if e.type == "model.called"]


# ---- 运行时 ----

async def test_view_is_the_only_channel(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path)
    run.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")
    run.board.put("manager/thesis", {"rating": "Buy"}, by="manager")
    run.board.put("debate/bull", "CANARY-不该被交易员看到", by="bull")
    await run_role(run, TRADER)
    event = called(run)[0]
    sent = json.dumps(event.data, ensure_ascii=False)
    assert "189.4" in sent
    assert "你是交易员。" in sent                       # 指令随请求一起记录
    assert "CANARY" not in sent
    assert run.board.get("trader/proposal", Proposal).entry == 189.5
    assert event.caused_by == (1, 2)


async def test_one_corrective_retry_then_malformed(tmp_path):
    run = make_run({"trader": ['```json\n{"action": "Buy"}\n```', GOOD]}, tmp_path)
    assert (await run_role(run, TRADER)).stop == 181.0
    event = called(run)[0]
    assert event.data["usage"] == {"requests": 2, "input_tokens": 2000, "output_tokens": 600}
    assert "entry" in json.dumps(event.data["messages"], ensure_ascii=False)   # 纠正提示指出了缺的字段
    assert run.budget.table()[0]["requests"] == 2

    run = make_run({"trader": ["not json at all"]}, tmp_path)
    with pytest.raises(Malformed):
        await run_role(run, TRADER)
    assert not run.board.has("trader/proposal")
    assert called(run)[0].data["output"] is None       # 失败的调用也记账、也留痕
    assert run.budget.tokens == 2600


@pytest.mark.parametrize("mode", ["auto", "native", "tool", "prompted"])
async def test_every_output_mode_gives_the_same_artifact(tmp_path, mode):
    run = make_run({"trader": [GOOD]}, tmp_path, output_mode=mode)
    assert (await run_role(run, TRADER)).cites == ("close", "atr_14")
    assert run.board.get("trader/proposal") == Proposal.model_validate(GOOD).model_dump(mode="json")


async def test_fold_equals_live_and_replay_reproduces(tmp_path):
    run = make_run({"trader": [{"action": "Buy"}, GOOD]}, tmp_path)
    run.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")
    await run_role(run, TRADER)
    events = Journal.load(tmp_path / "journal.jsonl")
    assert Board.fold(events).snapshot() == run.board.snapshot()

    journal2 = Journal()
    run2 = Run("r2", journal2, Board(journal2), Replay(events), Budget())
    run2.board.put("ledger/price", [{"id": "close", "value": 189.4}], by="ledger")
    await run_role(run2, TRADER)
    assert run2.board.snapshot() == run.board.snapshot()
    assert run2.budget.tokens == run.budget.tokens

    journal3 = Journal()
    run3 = Run("r3", journal3, Board(journal3), Replay(events), Budget())
    run3.board.put("ledger/price", [{"id": "close", "value": 190.0}], by="ledger")   # 输入变了
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(run3, TRADER)


async def test_budget_halts_without_calling(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path, ceiling=Ceiling(tokens=2000))
    await run_role(run, TRADER)                        # 1300，未超
    await run_role(run, TRADER)                        # 2600，超了，但这次调用已发生，结果保留
    with pytest.raises(Halt) as e:
        await run_role(run, TRADER)
    assert (e.value.reason, e.value.detail) == ("budget_exhausted", "run")
    with pytest.raises(Halt):
        await run_role(run, TRADER)
    types = [e.type for e in run.journal.events]
    assert types.count("model.called") == 2
    assert types.count("budget.exceeded") == 1


async def test_request_and_time_limits(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path, limits=Limits(max_requests=2))
    await run_role(run, TRADER)
    await run_role(run, TRADER)
    with pytest.raises(Halt, match="calls_exhausted"):
        await run_role(run, TRADER)

    ticks = iter([0.0, 0.0, 5.0, 11.0])
    run = make_run({"trader": [GOOD]}, tmp_path, limits=Limits(max_seconds=10), clock=lambda: next(ticks))
    await run_role(run, TRADER)
    await run_role(run, TRADER)
    with pytest.raises(Halt, match="time_exhausted"):
        await run_role(run, TRADER)


async def test_a_role_with_no_model_halts_as_unavailable(tmp_path):
    run = make_run({}, tmp_path)
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(run, TRADER)
    assert [e.type for e in run.journal.events] == ["model.failed"]


async def test_empty_list_renders_as_marker(tmp_path):
    run = make_run({"trader": [GOOD]}, tmp_path)
    run.board.put("ledger/price", [], by="ledger")
    await run_role(run, TRADER)
    assert "（空）" in called(run)[0].data["prompt"]


# ---- 日志与黑板 ----

def test_journal_rejects_an_unknown_event_type():
    journal = Journal()
    with pytest.raises(ValidationError):
        journal.append("something.else")              # type: ignore[arg-type]
    assert journal.events == ()


def test_board_holds_plain_data_and_validates_on_read(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    board = Board(journal)
    board.put("trader/proposal", Proposal.model_validate(GOOD), by="trader")
    assert board.get("trader/proposal")["cites"] == ["close", "atr_14"]
    assert board.get("trader/proposal", Proposal).cites == ("close", "atr_14")
    board.put("x/list", [Claim(fact="a", value=1.0, reading="r")], by="x")
    assert board.get("x/list", list[Claim])[0].fact == "a"
    with pytest.raises(ValidationError):
        board.get("x/list", Proposal)
    with pytest.raises(RuntimeError):
        Board.fold(journal.events).put("a/b", 1, by="x")


# ---- 预算 ----

def usage(i: int, o: int) -> RunUsage:
    return RunUsage(input_tokens=i, output_tokens=o, requests=1)


def test_lower_ceiling_wins_and_groups():
    batch = Budget(Ceiling(tokens=1500), name="batch")
    run = Budget(Ceiling(tokens=10_000), groups={"judge": 1000}, parent=batch)
    run.record("judge.evidence", "m", usage(600, 100), None)
    assert run.exceeded("judge") is None
    run.record("judge.risk", "m", usage(600, 100), None)
    assert run.exceeded("judge") == "role:judge"
    assert run.exceeded("trader") is None
    run.record("trader", "m", usage(100, 100), None)
    assert run.exceeded("trader") == "batch"


def test_exactly_at_ceiling_is_not_a_breach():
    b = Budget(Ceiling(tokens=1300))
    b.record("x", "m", usage(1000, 300), None)
    assert b.exceeded() is None


def test_cost_ceiling_needs_a_price():
    b = Budget(Ceiling(cost=0.01))
    b.record("x", "m", usage(1000, 500), 0.006)
    assert b.exceeded() is None
    b.record("x", "m", usage(1000, 500), 0.006)
    assert b.exceeded() == "run"
    with pytest.raises(BudgetConfigError):
        Budget(Ceiling(cost=1.0)).record("x", "unknown", usage(1, 1), None)
    Budget().record("x", "unknown", usage(1, 1), None)
