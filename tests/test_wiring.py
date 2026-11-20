"""装配的测试：用户输入在边界上被校验；运行目录名里没有用户原文；
日志记下实际生效的预算，所以任何一次运行（包括预算用尽的）都能从自己的日志重放。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from insightcrew.app import replay as replay_cmd
from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT, BadRequest, describe_models, open_run, prepare, write_page
from insightcrew.domain.types import Outcome
from insightcrew.evals.scenarios import load_one
from insightcrew.flows.analyze import analyze

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_.]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{4}$")


@pytest.fixture
def settings() -> Settings:
    return load(ROOT, force_offline=True)


@pytest.fixture
def with_model() -> Settings:
    """一份能构造出真实模型的设置：裸模型名加端点与密钥。构造不联网。"""
    return load(ROOT, tiers={"quick": "some-model", "deep": "some-model"},
                base_url="http://127.0.0.1:9/v1", api_key="sk-test")


def _run(settings: Settings, runs: Path, *, ticker: str = "", scenario: str = "") -> tuple[Path, Outcome]:
    job = prepare(settings, ticker=ticker, scenario=scenario)
    run, deps = open_run(settings, job.request, scenario=job.scenario, flow=job.flow, runs_dir=runs)
    outcome = asyncio.run(analyze(run, job.request, deps, job.flow))
    write_page(run, deps)
    assert deps.out_dir is not None
    return deps.out_dir, outcome


# ---- prepare：把用户输入变成任务 ----

def test_nothing_given_and_no_model_means_the_demo_scenario(settings: Settings) -> None:
    job = prepare(settings)
    assert job.scenario is not None and job.scenario.name == "demo"
    assert job.request == {"ticker": "ACME", "as_of": "2026-09-30"}


def test_nothing_given_with_a_model_is_refused(with_model: Settings) -> None:
    assert describe_models(with_model)[0] == "model"
    with pytest.raises(BadRequest, match="没有给出代码"):
        prepare(with_model)
    job = prepare(with_model, ticker="ACME")
    assert job.scenario is None and job.request == {"ticker": "ACME"}


def test_a_scenario_supplies_request_and_flow_and_the_user_overrides_them(settings: Settings) -> None:
    job = prepare(settings, scenario="thin")
    assert job.request == {"ticker": "THIN", "as_of": "2026-09-30"}
    assert job.flow == load_one("thin").config.flow

    job = prepare(settings, scenario="demo", ticker=" STALE ", as_of="2026-08-31", judges=False, debate_rounds=0)
    assert job.request == {"ticker": "STALE", "as_of": "2026-08-31"}
    assert job.flow.judges is False and job.flow.debate_rounds == 0
    assert job.flow.audit_rounds == load_one("demo").config.flow.audit_rounds


def test_without_a_scenario_the_flow_comes_from_settings() -> None:
    settings = load(ROOT, force_offline=True, flow={"judge_rounds": 5})
    assert prepare(settings, ticker="ACME").flow.judge_rounds == 5
    assert prepare(settings, ticker="ACME", judges=False).flow.judges is False


@pytest.mark.parametrize("text", ["2026-13-45", "../../x", "yesterday", "20260930x"])
def test_an_unparsable_date_is_refused(settings: Settings, text: str) -> None:
    with pytest.raises(BadRequest, match="无法解析"):
        prepare(settings, ticker="ACME", as_of=text)


def test_an_unknown_scenario_is_refused_and_the_real_ones_are_named(settings: Settings) -> None:
    with pytest.raises(BadRequest, match="demo"):
        prepare(settings, scenario="no-such-scenario")


# ---- open_run：开一次运行 ----

def test_open_run_leaves_the_request_alone_and_names_the_directory(settings: Settings, tmp_path: Path) -> None:
    request = {"ticker": "acme"}
    run, deps = open_run(settings, request, runs_dir=tmp_path)
    assert request == {"ticker": "acme"}
    assert deps.out_dir == tmp_path / run.id and RUN_ID.fullmatch(run.id)
    assert "-ACME-2026-09-30-" in run.id           # 没给日期：目录名用准入将采用的那一天，不是今天
    started = run.journal.events[0]
    assert started.type == "run.started" and started.data["request"] == {"ticker": "acme"}
    assert started.data["mode"] == "scripted"


@pytest.mark.parametrize("ticker", ["../../etc", "BRK.B", "a/b", ""])
def test_user_text_never_reaches_a_path(settings: Settings, tmp_path: Path, ticker: str) -> None:
    if not ticker:
        with pytest.raises(BadRequest):
            open_run(settings, {"ticker": ticker}, runs_dir=tmp_path)
        assert list(tmp_path.iterdir()) == []
        return
    run, _ = open_run(settings, {"ticker": ticker, "as_of": "2026-09-30"}, runs_dir=tmp_path)
    assert RUN_ID.fullmatch(run.id) and "-INVALID-2026-09-30-" in run.id
    assert [p.name for p in tmp_path.iterdir()] == [run.id]


def test_a_bad_date_creates_nothing(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(BadRequest):
        open_run(settings, {"ticker": "ACME", "as_of": "../../x"}, runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path.parent / "x").exists()


def test_run_started_records_the_ceilings_actually_in_force(settings: Settings, tmp_path: Path) -> None:
    plain, _ = open_run(settings, {"ticker": "ACME"}, runs_dir=tmp_path)
    assert plain.journal.events[0].data["ceilings"] == {
        "run_tokens": settings.budget.run_tokens, "run_cost": None, "roles": {}}
    scenario = load_one("budget_early")
    tight, _ = open_run(settings, dict(scenario.request), scenario=scenario, runs_dir=tmp_path)
    assert tight.journal.events[0].data["ceilings"]["run_tokens"] == scenario.config.budget.run_tokens
    assert tight.budget.ceiling.tokens == scenario.config.budget.run_tokens


def test_the_journal_holds_no_key_and_no_path(with_model: Settings, tmp_path: Path) -> None:
    run, _ = open_run(with_model, {"ticker": "ACME"}, runs_dir=tmp_path)
    text = (tmp_path / run.id / "journal.jsonl").read_text(encoding="utf-8")
    assert "sk-test" not in text and "127.0.0.1" not in text and str(tmp_path) not in text
    assert json.loads(text)["data"]["models"] == {"quick": "some-model", "deep": "some-model"}


# ---- 一次运行留下什么，以及它能否重放 ----

def test_a_delivered_run_leaves_the_journal_and_its_three_projections(settings: Settings, tmp_path: Path) -> None:
    run_dir, outcome = _run(settings, tmp_path)
    assert (outcome.status, outcome.judgment) == ("delivered", "accepted 2/3")
    assert {p.name for p in run_dir.iterdir()} == {"journal.jsonl", "report.md", "board.json", "replay.html"}
    page = (run_dir / "replay.html").read_text(encoding="utf-8")
    slot = re.search(r'id="embedded">(.*?)</script>', page, re.S)
    assert slot is not None
    embedded = json.loads(slot.group(1))
    lines = (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert [e["seq"] for e in embedded["events"]] == list(range(1, len(lines) + 1))
    assert 200 <= len(embedded["bars"]) <= 250 and embedded["bars"][-1]["date"] == "2026-09-30"


def test_a_refused_run_has_no_model_call_and_no_price_series(settings: Settings, tmp_path: Path) -> None:
    run_dir, outcome = _run(settings, tmp_path, ticker="THIN")
    assert (outcome.status, outcome.reason) == ("review", "insufficient_history")
    journal = (run_dir / "journal.jsonl").read_text(encoding="utf-8")
    assert "model.called" not in journal
    assert '"bars": null' in (run_dir / "replay.html").read_text(encoding="utf-8")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "REVIEW：未给出评级" in report and "不足下限 60 根" in report


@pytest.mark.parametrize("given", [{}, {"scenario": "budget_early"}, {"scenario": "budget_late"},
                                   {"scenario": "contested"}, {"scenario": "malformed_judge"},
                                   {"ticker": "THIN"}, {"ticker": "BRK.B"}, {"ticker": "ACME"}])
def test_every_kind_of_run_replays_from_its_own_journal(settings: Settings, tmp_path: Path,
                                                        given: dict[str, str],
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    run_dir, _ = _run(settings, tmp_path / "runs", **given)
    assert replay_cmd.run(settings, str(run_dir)) == 0, capsys.readouterr().out
    assert [p.name for p in (tmp_path / "runs").iterdir()] == [run_dir.name]      # 重放不留下新的运行目录


def test_replay_reports_a_journal_that_was_tampered_with(settings: Settings, tmp_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    run_dir, _ = _run(settings, tmp_path)
    journal = run_dir / "journal.jsonl"
    journal.write_text(journal.read_text(encoding="utf-8").replace("accepted 2/3", "accepted 3/3"), encoding="utf-8")
    assert replay_cmd.run(settings, str(run_dir)) == 1
    assert "不一致" in capsys.readouterr().out


# ---- 证据通道的 prepare（BLUEPRINT 18.8、18.11）----


def test_channels_flag_reaches_the_flow(settings: Settings) -> None:
    job = prepare(settings, ticker="ACME", channels=("disclosures",))
    assert job.flow.channels == ("disclosures",)
    both = prepare(settings, ticker="ACME", channels=("disclosures", "fundamentals"))
    assert both.flow.channels == ("fundamentals", "disclosures")     # 固定顺序，去重


def test_a_scenario_decides_the_channels_and_the_flag_is_ignored(settings: Settings) -> None:
    job = prepare(settings, ticker="ACME", scenario="channels_clean",
                  channels=("disclosures",))
    assert job.flow.channels == ("fundamentals", "disclosures")      # 场景文件说了算
    no_channels = prepare(settings, scenario="clean", channels=("disclosures",))
    assert no_channels.flow.channels == ()                           # 场景没开通道就不开


def test_an_unknown_channel_is_refused(settings: Settings) -> None:
    with pytest.raises(BadRequest, match="nope"):     # news 自 v2.2 起是合法通道
        prepare(settings, ticker="ACME", channels=("nope",))


# ---- [flow] channels 设置（BLUEPRINT 20.7 第 7 条：缺陷 D4）----


def test_the_settings_channels_apply_when_the_flag_is_absent() -> None:
    """D4：设置里开了通道、没给 --channels 时，任务带着那条通道（修复前 prepare 总拿空元组覆盖它）。"""
    settings = load(ROOT, force_offline=True, flow={"channels": ["fundamentals"]})
    assert prepare(settings, ticker="ACME").flow.channels == ("fundamentals",)


def test_the_channels_flag_overrides_the_settings_and_empty_means_none() -> None:
    settings = load(ROOT, force_offline=True, flow={"channels": ["fundamentals"]})
    assert prepare(settings, ticker="ACME", channels=("disclosures",)).flow.channels == ("disclosures",)
    assert prepare(settings, ticker="ACME", channels=()).flow.channels == ()    # 显式点名"一个也不开"


def test_a_scenario_ignores_both_the_settings_channels_and_the_flag() -> None:
    settings = load(ROOT, force_offline=True, flow={"channels": ["fundamentals"]})
    job = prepare(settings, scenario="clean", channels=("disclosures",))
    assert job.flow.channels == load_one("clean").config.flow.channels == ()    # 场景说了算


def test_build_deps_wires_the_evidence_ports(settings: Settings) -> None:
    from insightcrew.app.wiring import build_deps
    deps = build_deps(settings)
    assert deps.statements is not None and deps.disclosures is not None
    assert len(deps.fundamentals) == 12
    assert (deps.disclosure_window_days, deps.disclosure_max_items) == (90, 30)
