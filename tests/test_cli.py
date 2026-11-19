"""命令行的测试：WP5 的验收条目写成测试；用户敲错的东西得到一行说明与退出码 2，不是回溯。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from insightcrew.app import wiring
from insightcrew.app.cli import app

RATING_WORDS = ("Buy", "Sell", "Overweight", "Underweight")


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """命令行没有换运行目录的选项：测试把装配里的默认根目录换成临时目录，并强制离线。"""
    monkeypatch.setattr(wiring, "RUNS", tmp_path)
    monkeypatch.setenv("IC_FORCE_OFFLINE", "true")
    return tmp_path


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result.exit_code, result.output


def _only_run(runs: Path) -> Path:
    (run_dir,) = runs.iterdir()
    return run_dir


def test_analyze_with_nothing_runs_the_demo_and_writes_four_files(runs: Path) -> None:
    code, output = _invoke("analyze", "--no-open")
    assert code == 0
    assert "未使用真实模型" in output and "demo" in output           # 回退到离线从不静默
    assert "结果：delivered（原因 ok）；判断：accepted 2/3；核验：verified" in output
    assert "闸门 审计：发现 1 个问题（max_risk_per_trade_pct）" in output
    assert "打回：从 交易员 重跑" in output
    assert {p.name for p in _only_run(runs).iterdir()} == {"journal.jsonl", "report.md", "board.json", "replay.html"}


def test_quiet_prints_the_outcome_only(runs: Path) -> None:
    _, output = _invoke("analyze", "--scenario", "clean", "--no-open", "--quiet")
    assert [line[:3] for line in output.splitlines()] == ["结果：", "运行目"]


def test_thin_is_review_without_a_model_call(runs: Path) -> None:
    code, output = _invoke("analyze", "THIN", "--no-open")
    assert code == 0
    assert "结果：REVIEW（原因：insufficient_history）" in output
    journal = (_only_run(runs) / "journal.jsonl").read_text(encoding="utf-8")
    assert "model.called" not in journal and "model.failed" not in journal


def test_an_invalid_ticker_is_review_not_a_traceback(runs: Path) -> None:
    code, output = _invoke("analyze", "BRK.B", "--no-open")
    assert code == 0 and "结果：REVIEW（原因：invalid_ticker）" in output
    assert "-INVALID-" in _only_run(runs).name


def test_a_ticker_with_no_scenario_and_no_model_names_the_scenarios(runs: Path) -> None:
    _, output = _invoke("analyze", "ACME", "--no-open")
    assert "结果：REVIEW（原因：model_unavailable）" in output
    assert "离线场景" in output and "demo" in output


def test_the_contested_report_has_no_rating_and_no_level(runs: Path) -> None:
    code, output = _invoke("analyze", "--scenario", "contested", "--no-open", "--quiet")
    assert code == 0 and "结果：REVIEW（原因：contested）" in output
    run_dir = _only_run(runs)
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    head, _, rest = report.partition("## 多空观点")
    outside = head + rest.partition("## 风控检查")[2]
    assert all(word not in outside for word in RATING_WORDS)
    proposal = json.loads((run_dir / "board.json").read_text(encoding="utf-8"))["trader/proposal"]
    assert proposal["entry"] and proposal["stop"]                   # 方案还在黑板上，只是不进报告
    assert all(label not in report for label in ("入场价", "止损价", "目标价"))
    for level in (proposal["stop"], proposal["target"]):            # 入场价就是收盘价，事实表里本来就有
        assert f"{level:g}" not in report


def test_pinning_today_does_not_change_the_board(runs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _invoke("analyze", "--no-open", "--quiet")
    first = (_only_run(runs) / "board.json").read_bytes()
    monkeypatch.setenv("IC_TODAY", "2027-01-01")
    _invoke("analyze", "--no-open", "--quiet")
    boards = [(p / "board.json").read_bytes() for p in runs.iterdir()]
    assert len(boards) == 2 and boards[0] == boards[1] == first


def test_flow_flags_reach_the_run(runs: Path) -> None:
    _, output = _invoke("analyze", "--scenario", "clean", "--no-judges", "--debate-rounds", "0", "--no-open")
    assert "bull#1" not in output and "judge." not in output
    assert "判断：not reviewed: judges disabled" in output


@pytest.mark.parametrize(("args", "said"), [
    (("analyze", "ACME", "--date", "2026-13-45", "--no-open"), "无法解析"),
    (("analyze", "ACME", "--date", "../../x", "--no-open"), "无法解析"),
    (("analyze", "--scenario", "no-such-scenario", "--no-open"), "没有名为"),
    (("analyze", "ACME", "--universe", "no-such-universe", "--no-open"), "读不出来"),
    (("facts", "ACME", "--date", "tomorrow"), "无法解析"),
    (("scenarios", "no-such-scenario"), "没有名为"),
])
def test_what_the_user_got_wrong_is_one_line_and_exit_2(runs: Path, args: tuple[str, ...], said: str) -> None:
    code, output = _invoke(*args)
    assert code == 2 and said in output
    assert "Traceback" not in output
    assert list(runs.iterdir()) == []              # 被拒绝的请求什么都不创建


def test_a_mistyped_setting_is_named(runs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IC_FLOW__JUDGEZ", "false")
    code, output = _invoke("analyze", "--no-open")
    assert code == 2 and "设置无效" in output and "judgez" in output.lower()


def test_facts_prints_the_ledger_and_the_data_card(runs: Path) -> None:
    code, output = _invoke("facts", "ACME", "--date", "2026-09-30")
    assert code == 0
    assert "分析日 2026-09-30" in output and "rsi_14" in output and "复算分歧：无" in output
    # 账本的 29 个事实逐一打印（通道的内容另有断言，见 test_facts_prints_the_channels）。
    assert len([line for line in output.splitlines() if "indicators." in line]) == 29
    code, output = _invoke("facts", "NOPE")
    assert code == 1 and "无法建立账本" in output


def test_facts_prints_the_channels(runs: Path) -> None:
    """18.11：facts 在账本之后打印基本面事实与卡片，以及窗口内的公告条目。"""
    code, output = _invoke("facts", "ACME", "--date", "2026-09-30")
    assert code == 0
    assert "基本面通道：可用" in output
    assert "revenue_fy" in output and "1081" in output and "USD million" in output
    assert "重述提醒" in output
    assert "公告通道：可用" in output
    assert "董事会批准股份回购计划" in output and "窗口 2026-07-02 到 2026-09-30，3 条" in output
    _, hk = _invoke("facts", "0000.HK", "--date", "2026-06-30")
    assert "基本面通道：不可用" in hk and "公告通道：窗口内没有公告" in hk


def test_scenarios_all_match_and_leave_no_run_behind(runs: Path) -> None:
    code, output = _invoke("scenarios")
    assert code == 0
    lines = output.splitlines()
    assert len(lines) >= 16 and all(line.startswith("✓") and line.endswith("相符") for line in lines)
    assert list(runs.iterdir()) == []


def test_eval_all_runs_the_three_offline_evaluations(runs: Path) -> None:
    code, output = _invoke("eval", "all")
    assert code == 0 and "19/19" in output and "0/6" in output


# ---- 证据通道的旗标（BLUEPRINT 18.11）----


def test_an_unknown_channel_name_is_refused(runs: Path) -> None:
    code, output = _invoke("analyze", "ACME", "--channels", "news", "--no-open")
    assert code == 2 and "news" in output


def test_channels_flag_on_a_scenario_run_says_it_is_ignored(runs: Path) -> None:
    code, output = _invoke("analyze", "--scenario", "clean", "--channels", "fundamentals",
                           "--no-open", "--quiet")
    assert code == 0
    assert "场景运行以场景文件里写的通道为准" in output


def test_channels_open_the_analysts_in_a_scenario(runs: Path) -> None:
    code, output = _invoke("analyze", "--scenario", "channels_clean", "--no-open")
    assert code == 0
    assert "fundamentals#1" in output and "disclosures#1" in output
    assert "结果：delivered" in output
