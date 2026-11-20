"""app/verify.py 的测试（BLUEPRINT 20.7 第 12 条）。

八条结构检查各有一个反例；好的日志与未结束的日志通过；一次真实交付运行通过，
删掉中间一行后失败（WP42 验收）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from insightcrew.app import verify as verify_cmd
from insightcrew.app.cli import app
from insightcrew.app.settings import load
from insightcrew.app.wiring import ROOT, open_run, prepare
from insightcrew.flows.analyze import analyze
from insightcrew.kernel.events import Journal

USAGE_ROW = {"role": "technical", "model": "scripted", "calls": 1, "requests": 1,
             "input_tokens": 10, "output_tokens": 5, "cost": None}


def _write_run(tmp_path: Path, name: str, *, finished: bool = True, outcome: bool = True) -> Path:
    """一份最小而结构完好的日志：一次成功的模型调用、用量与结果，可选收尾。"""
    run_dir = tmp_path / name
    run_dir.mkdir()
    journal = Journal(run_dir / "journal.jsonl")
    journal.append("run.started", actor="app", data={"request": {"ticker": "ACME"}})
    journal.append("artifact.written", actor="intake",
                   data={"key": "request/scope", "version": 1, "value": {}})
    called = journal.append("model.called", actor="technical", step="technical#1",
                            data={"model": "scripted", "output": {"claims": []},
                                  "usage": {"requests": 1, "input_tokens": 10,
                                            "output_tokens": 5},
                                  "cost": None})
    journal.append("artifact.written", actor="technical", step="technical#1",
                   caused_by=[called.seq],
                   data={"key": "technical/analysis", "version": 1, "value": {}})
    if outcome:
        journal.append("artifact.written", actor="flow",
                       data={"key": "run/outcome", "version": 1, "value": {}})
        journal.append("artifact.written", actor="flow",
                       data={"key": "run/usage", "version": 1, "value": [dict(USAGE_ROW)]})
    if finished:
        journal.append("run.finished", actor="flow", data={"status": "delivered", "reason": "ok"})
    return run_dir


def _events(run_dir: Path) -> list[dict[str, Any]]:
    text = (run_dir / "journal.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _rewrite(run_dir: Path, events: list[dict[str, Any]]) -> None:
    (run_dir / "journal.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8")


def _verify(run_dir: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = verify_cmd.run(str(run_dir))
    return code, capsys.readouterr().out


# ---- 好的日志 ----


def test_a_well_formed_journal_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _verify(_write_run(tmp_path, "good"), capsys)
    assert code == 0 and "通过" in out and "未结束" not in out


def test_an_unfinished_journal_passes_with_the_note(tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _verify(_write_run(tmp_path, "open", finished=False, outcome=False), capsys)
    assert code == 0 and "通过" in out and "未结束" in out


def test_the_cli_command_passes_on_a_good_run(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, "cli")
    result = CliRunner().invoke(app, ["verify", str(run_dir)])
    assert result.exit_code == 0 and "通过" in result.output


# ---- 八条检查，每条一个反例 ----


def test_a_sequence_gap_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "gap")
    events = _events(run_dir)
    del events[3]                                       # 中间一行被删：序号断档
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "序号" in out


def test_a_journal_not_starting_with_run_started_fails(tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "first")
    events = _events(run_dir)
    events[0]["type"] = "gate.checked"
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "第一条" in out


def test_run_finished_twice_or_not_last_fails(tmp_path: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "twice")
    events = _events(run_dir)
    events.append(dict(events[-1]))                     # 第二个 run.finished
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "run.finished" in out

    run_dir = _write_run(tmp_path, "notlast")
    events = _events(run_dir)
    events.append({"seq": len(events) + 1, "ts": "", "type": "gate.checked",
                   "step": "", "actor": "audit", "data": {}, "caused_by": ()})
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "最后" in out


def test_caused_by_naming_a_missing_or_later_event_fails(tmp_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    for name, seq, word in (("missing", 99, "不存在"), ("later", 2, "不更早")):
        run_dir = _write_run(tmp_path, name)
        events = _events(run_dir)
        events[1]["caused_by"] = [seq]                  # request/scope 的 caused_by
        _rewrite(run_dir, events)
        code, out = _verify(run_dir, capsys)
        assert code == 1 and word in out


def test_a_skipped_artifact_version_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "version")
    events = _events(run_dir)
    events[3]["data"]["version"] = 2                    # 第一版就写成 2
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "版本" in out


def test_a_model_reply_without_its_artifact_fails(tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "orphan")
    events = _events(run_dir)
    events[3]["caused_by"] = []                         # model.called 的回复不再有产物承接
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "artifact.written" in out


def test_usage_that_differs_from_the_model_called_sums_fails(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "usage")
    events = _events(run_dir)
    events[5]["data"]["value"][0]["requests"] = 2
    _rewrite(run_dir, events)
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "run/usage" in out


def test_outcome_without_run_finished_fails(tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "dangling", finished=False)      # 有 run/outcome、没有收尾
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "run/outcome" in out


def test_a_line_that_does_not_parse_fails(tmp_path: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _write_run(tmp_path, "torn")
    with (run_dir / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"seq": 99, "type": "run.fi')          # 残行：verify 不截，报为问题
    code, out = _verify(run_dir, capsys)
    assert code == 1 and "不是合法 JSON" in out


# ---- 一次真实运行 ----


async def test_a_real_delivered_run_passes_and_a_deleted_middle_line_fails(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = load(ROOT, force_offline=True)
    job = prepare(settings, scenario="clean")
    run, deps = open_run(settings, job.request, scenario=job.scenario, flow=job.flow,
                         runs_dir=tmp_path)
    await analyze(run, job.request, deps, job.flow)
    assert deps.out_dir is not None
    code, out = _verify(deps.out_dir, capsys)
    assert code == 0 and "通过" in out

    journal = deps.out_dir / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines()
    del lines[len(lines) // 2]                          # 删中间一行
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, out = _verify(deps.out_dir, capsys)
    assert code == 1
