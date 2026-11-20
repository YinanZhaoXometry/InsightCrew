"""审批门（WP52，BLUEPRINT 20.11）的测试。

三层各测各的：
    图（替身阶段）：interrupt 真的把运行暂停在门口（ApprovalPending、日志没有 run.finished、
    暂停之后没有模型调用）；决定经 drive(decision=…) 或 drive(ask=…) 从暂停的检查点续起；
    接受照常交付，拒绝以 REVIEW（rejected_by_user）收尾；approve 关着时图里没有 approval 节点。
    装配（wiring）：场景把决定写在自己的 replies["user"] 里先上板；开了门却没带决定的场景被
    prepare 拒绝；重放从日志取回决定（replay 命令对两个场景的运行都「一致」）。
    网页（server）：POST /api/runs/<id>/approval 的接受与拒绝、404/409/422 的拒绝，
    暂停的事件流不带 error 收尾，跑完的日志过 verify。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from insightcrew.adapters.scripted import Script
from insightcrew.app import replay as replay_cmd
from insightcrew.app import verify as verify_cmd
from insightcrew.app import wiring
from insightcrew.app.server import create_app
from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT, open_run
from insightcrew.domain.types import Approval
from insightcrew.evals.scenarios import load_one, run_one
from insightcrew.flows import analyze as flow
from insightcrew.flows import stages as real_stages
from insightcrew.flows.analyze import ApprovalPending, FlowConfig, analyze, drive
from insightcrew.flows.graph import audit_map, build_graph
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
YES = {"accept": True, "target": "none", "critique": "", "cites": []}

REPLIES = {"technical": [ANALYSIS], "bull": [ARG], "bear": [ARG], "manager": [THESIS],
           "trader": [GOOD], "judge.evidence": [YES], "judge.conservative": [YES],
           "judge.aggressive": [YES]}
REQUEST = {"ticker": "ACME", "as_of": "2026-09-30"}
APPROVED = Approval(decision="approved", note="按方案执行")
REJECTED = Approval(decision="rejected", note="不想现在建仓")


@pytest.fixture(autouse=True)
def _use_fake_stages(monkeypatch):
    monkeypatch.setattr(flow, "stages", fake_stages)


def _run(replies: dict | None = None) -> Run:
    journal = Journal()
    return Run("t", journal, Board(journal), Script({**REPLIES, **(replies or {})}), Budget())


def _approval_events(run: Run) -> list[Any]:
    return [e for e in run.journal.events
            if e.type == "artifact.written" and e.data["key"] == "approval/decision"]


# ---- 图：暂停、续起、两种决定 ----

async def test_the_gate_pauses_the_run_and_waits_for_a_decision():
    """approve 开着而没有人能给决定：ApprovalPending；日志没有 run.finished，没有 run/outcome，
    暂停之后不再有模型调用（等待不花任何上限）。"""
    run, cfg = _run(), FlowConfig(approve=True)
    saver = InMemorySaver()
    with pytest.raises(ApprovalPending):
        await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate")
    assert not [e for e in run.journal.events if e.type == "run.finished"]
    assert "run/outcome" not in run.board.snapshot()
    assert "approval/decision" not in run.board.snapshot()
    actors = [e.actor for e in run.journal.events if e.type == "model.called"]
    assert actors[-1].startswith("judge.")                # 最后一次调用是评审：门在其后
    assert not [e for e in run.journal.events if e.type == "gate.checked"
                and e.data.get("gate") == "approval"]     # 门口没有新的闸门或调用


async def test_a_paused_run_resumes_with_an_approved_decision():
    """决定经 drive(decision=…) 从暂停的检查点续起（LangGraph 的 Command(resume=…)）：
    同一 Run、同一线程，接受后照常交付；approval/decision 恰好写一次，actor 是 user。"""
    run, cfg = _run(), FlowConfig(approve=True)
    saver = InMemorySaver()
    with pytest.raises(ApprovalPending):
        await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate")
    outcome = await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate",
                          decision=APPROVED)
    assert outcome.status == "delivered" and outcome.reason == "ok"
    assert outcome.judgment == "accepted 2/3"
    assert run.board.get("approval/decision", Approval) == APPROVED
    events = _approval_events(run)
    assert len(events) == 1 and events[0].actor == "user"
    assert [e.type for e in run.journal.events][-1] == "run.finished"


async def test_a_rejected_decision_ends_review_with_rejected_by_user():
    """拒绝：运行以 REVIEW、原因 rejected_by_user 结束；方案已过审计，核验仍是 verified；
    判断照录评审已给的结论（问题 45）。"""
    run, cfg = _run(), FlowConfig(approve=True)
    saver = InMemorySaver()
    with pytest.raises(ApprovalPending):
        await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate")
    outcome = await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate",
                          decision=REJECTED)
    assert outcome.status == "review" and outcome.reason == "rejected_by_user"
    assert outcome.facts_and_rules == "verified" and outcome.judgment == "accepted 2/3"
    assert run.board.get("approval/decision", Approval) == REJECTED
    assert [e.type for e in run.journal.events][-1] == "run.finished"


async def test_ask_answers_the_gate_within_one_drive():
    """ask（命令行 --approve 的终端问询）：同一次 drive 里暂停后问到决定，续起并交付。
    Command(resume=…) 需要检查点：命令行与网页的真实入口（wiring.run_flow）都带它。"""
    run, cfg = _run(), FlowConfig(approve=True)
    saver = InMemorySaver()
    asked: list[Run] = []

    def ask(gate_run: Run) -> Approval:
        asked.append(gate_run)
        return APPROVED

    outcome = await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate", ask=ask)
    assert outcome.status == "delivered" and outcome.reason == "ok"
    assert asked and asked[0] is run                      # 问询拿到的是同一个 Run（黑板上有方案）
    assert run.board.get("approval/decision", Approval) == APPROVED


async def test_the_gate_sits_after_the_audit_when_judges_are_off():
    """评审关闭时门在审计之后：judges=False + approve=True 的运行照样暂停并接受。"""
    run, cfg = _run(), FlowConfig(approve=True, judges=False)
    saver = InMemorySaver()
    with pytest.raises(ApprovalPending):
        await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate")
    assert not [e for e in run.journal.events if e.actor.startswith("judge.")]
    outcome = await drive(run, REQUEST, None, cfg, checkpointer=saver, thread_id="gate",
                          decision=APPROVED)
    assert outcome.status == "delivered" and outcome.judgment == "not reviewed: judges disabled"


def test_the_gate_is_a_node_only_when_approve_is_on():
    """兼容规则（20.4）：approve 关着（默认）时图里没有 approval 节点，审计通过直达 outcome。"""
    off = {n.name for n in build_graph(fake_stages, FlowConfig(), None).get_graph().nodes.values()}
    assert "approval" not in off
    on = {n.name for n in build_graph(fake_stages, FlowConfig(approve=True),
                                      None).get_graph().nodes.values()}
    assert "approval" in on
    owners = ("technical", "manager", "trader")
    assert audit_map(FlowConfig(), owners)[-1] == "judges"                    # 默认：评审开着
    assert audit_map(FlowConfig(judges=False), owners)[-1] == "outcome"       # 评审关、门关：直达
    assert audit_map(FlowConfig(approve=True), owners)[-1] == "judges"        # 门在评审之后
    assert audit_map(FlowConfig(approve=True, judges=False), owners)[-1] == "approval"


# ---- 装配：场景自带决定、没带决定的拒绝、重放取回 ----

def test_a_scenarios_user_reply_reaches_the_board_before_the_flow(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """场景的 replies["user"] 是审批门的既定决定：open_run 先把它上板（actor user），
    图到门口时见到板上的决定就不再暂停——场景是一次完整的脚本化运行。"""
    monkeypatch.setattr(flow, "stages", real_stages)     # 走真实代码阶段（本模块默认用替身）
    settings = load(ROOT, force_offline=True)
    scenario = load_one("approval_approved")
    opener = partial(open_run, settings, runs_dir=tmp_path)
    result = asyncio.run(run_one(scenario, opener, analyze))
    assert result.ok, result.detail
    run_dir = next(p for p in tmp_path.iterdir() if (p / "journal.jsonl").is_file())
    events = [json.loads(line) for line in
              (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    approval = [e for e in events
                if e["type"] == "artifact.written" and e["data"]["key"] == "approval/decision"]
    assert len(approval) == 1 and approval[0]["actor"] == "user"
    assert approval[0]["data"]["value"] == {"decision": "approved", "note": "方案与失效条件都清楚，按方案执行。"}


def test_a_scenario_without_a_user_reply_pauses_rather_than_deciding(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """场景开了审批门却没在 replies 里写 user 的决定：运行停在门口（ApprovalPending），
    而不是替人决定——所以一次完整的脚本化场景必须自带决定（两个审批门场景都带了）。"""
    monkeypatch.setattr(flow, "stages", real_stages)
    settings = load(ROOT, force_offline=True)
    scenario = load_one("approval_approved").model_copy(
        update={"replies": {k: v for k, v in load_one("approval_approved").replies.items()
                            if k != "user"}})
    run, deps = open_run(settings, scenario.request, scenario=scenario,
                         flow=scenario.config.flow, runs_dir=tmp_path)
    with pytest.raises(ApprovalPending):
        asyncio.run(analyze(run, scenario.request, deps, scenario.config.flow))
    assert "approval/decision" not in run.board.snapshot()
    assert not [e for e in run.journal.events if e.type == "run.finished"]


@pytest.mark.parametrize("name", ["approval_approved", "approval_rejected"])
def test_an_approval_run_replays_identically(name: str, tmp_path: Path,
                                              capsys: pytest.CaptureFixture,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """重放按决策 44 从日志取回决定（重放端口在 wiring.open_run）：两个场景的运行都「一致」。"""
    monkeypatch.setattr(flow, "stages", real_stages)     # 重放走真实代码阶段
    settings = load(ROOT, force_offline=True)
    scenario = load_one(name)
    run, deps = open_run(settings, scenario.request, scenario=scenario,
                         flow=scenario.config.flow, runs_dir=tmp_path)
    outcome = asyncio.run(analyze(run, scenario.request, deps, scenario.config.flow))
    assert outcome.reason == ("ok" if name.endswith("approved") else "rejected_by_user")
    assert verify_cmd.run(str(tmp_path / run.id)) == 0
    assert replay_cmd.run(settings, str(tmp_path / run.id)) == 0
    assert "一致" in capsys.readouterr().out


# ---- 网页：确认按钮背后的路由 ----

@pytest.fixture
def settings_offline() -> Settings:
    return load(ROOT, force_offline=True, api_key="sk-secret-for-tests",
                base_url="http://secret-endpoint.example/v1")


@pytest.fixture
def app(settings_offline: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """无场景的运行配上 clean 场景的剧本：跑到审批门后暂停等人（场景路才会预置决定，
    这里特意走无场景的路，让门真的暂停）。"""
    monkeypatch.setattr(flow, "stages", real_stages)     # 网页运行走真实代码阶段
    real_models = wiring._models
    replies = load_one("clean").replies

    def _scripted(cur_settings: Settings, scenario: Any, replay: Any) -> tuple[Any, str, dict[str, str]]:
        if scenario is None and replay is None:
            return Script(replies), "scripted", {"quick": "scripted", "deep": "scripted"}
        return real_models(cur_settings, scenario, replay)

    monkeypatch.setattr(wiring, "_models", _scripted)
    return create_app(settings_offline, runs_dir=tmp_path)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _wait_for_pause(client: TestClient, run_id: str) -> None:
    """等运行停到门口：事件流以（无 error 的）interrupted 收尾。"""
    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.text.rstrip().endswith('data: {"error": null, "status": "interrupted"}')


def _journal(client: TestClient, run_id: str, tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in
            (tmp_path / run_id / "journal.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("decision,expect_status,expect_reason", [
    ("approved", "delivered", "ok"),
    ("rejected", "review", "rejected_by_user"),
])
def test_a_run_decided_over_http_ends_as_decided(client: TestClient, tmp_path: Path,
                                                 decision: str, expect_status: str,
                                                 expect_reason: str) -> None:
    run_id = _start_approval_run(client)
    _wait_for_pause(client, run_id)

    response = client.post(f"/api/runs/{run_id}/approval", json={"decision": decision, "note": ""})
    assert response.status_code == 202, response.text
    assert response.json() == {"run_id": run_id}

    # 重新接上事件流：等到收尾（end 报 finished），运行页面随后写盘
    deadline_text = client.get(f"/api/runs/{run_id}/events").text
    assert '"run.finished"' in deadline_text
    journal = _journal(client, run_id, tmp_path)
    assert journal[-1]["type"] == "run.finished"
    assert journal[-1]["data"] == {"status": expect_status, "reason": expect_reason}
    approval = [e for e in journal
                if e["type"] == "artifact.written" and e["data"]["key"] == "approval/decision"]
    assert len(approval) == 1 and approval[0]["actor"] == "user"
    assert approval[0]["data"]["value"]["decision"] == decision
    assert verify_cmd.run(str(tmp_path / run_id)) == 0
    assert client.get("/api/meta").json()["active_run"] is None


def _start_approval_run(client: TestClient) -> str:
    response = client.post("/api/runs", json={"ticker": "ACME", "approve": True})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def test_the_approval_endpoint_refuses(client: TestClient) -> None:
    assert client.post("/api/runs/20260101-000000-ACME-2026-09-30-abcd/approval",
                       json={"decision": "approved"}).status_code == 404      # 没有这个运行
    finished = client.post("/api/runs", json={"scenario": "demo"}).json()["run_id"]
    _watch_until_finished(client, finished)
    assert client.post(f"/api/runs/{finished}/approval",
                       json={"decision": "approved"}).status_code == 409      # 不在等待确认
    run_id = _start_approval_run(client)
    _wait_for_pause(client, run_id)
    assert client.post(f"/api/runs/{run_id}/approval",
                       json={"decision": "maybe"}).status_code == 422         # 只能接受或拒绝
    assert client.post(f"/api/runs/{run_id}/approval",
                       json={"decision": "approved"}).status_code == 202


def _watch_until_finished(client: TestClient, run_id: str) -> None:
    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert '"status": "finished"' in response.text


# ---- 命令行：终端问询 ----

def test_the_terminal_ask_reads_an_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    from insightcrew.app.cli import _ask_approval
    journal = Journal()
    run = Run("t", journal, Board(journal), Script({}), Budget())
    run.board.put("trader/proposal", GOOD, by="trader")
    answers = iter(["不确定", "a", "同意"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _ask_approval(run) == Approval(decision="approved", note="同意")   # 答错重问，再答接受
    answers = iter(["r", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _ask_approval(run) == Approval(decision="rejected", note="")
