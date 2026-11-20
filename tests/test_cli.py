"""命令行的测试：WP5 的验收条目写成测试；用户敲错的东西得到一行说明与退出码 2，不是回溯。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.models import Model
from typer.testing import CliRunner

from insightcrew.adapters.scripted import Script
from insightcrew.app import cli as cli_module
from insightcrew.app import resume as resume_cmd
from insightcrew.app import wiring
from insightcrew.app.cli import app
from insightcrew.app.settings import Settings, load
from insightcrew.evals.scenarios import load_one
from insightcrew.kernel.runtime import Role

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
    assert {p.name for p in _only_run(runs).iterdir()} == {"journal.jsonl", "report.md", "board.json",
                                                            "replay.html", "checkpoints.sqlite",
                                                            "trace.jsonl"}


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
    code, output = _invoke("analyze", "ACME", "--channels", "nope", "--no-open")   # news 自 v2.2 起合法
    assert code == 2 and "nope" in output


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


# ---- 意外中断与观察者（BLUEPRINT 20.7 第 10、11 条：缺陷 D6、问题 33）----


def test_analyze_reports_an_unexpected_stop_and_points_at_resume(runs: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """D6 的命令行一半：运行抛出意外异常时，页面照写（写当时已有的），一行说明、退出码 1。"""

    async def _boom(run: object, request: object, deps: object, flow: object = None,
                    **_kwargs: object) -> None:
        raise RuntimeError("模拟缺陷")

    monkeypatch.setattr(cli_module, "run_flow_with_checkpoints", _boom)
    code, output = _invoke("analyze", "--no-open")
    run_dir = _only_run(runs)
    assert code == 1
    assert "RuntimeError" in output and f"可用 insightcrew resume {run_dir} 续跑" in output
    assert (run_dir / "replay.html").exists()                  # 页面为已存在的部分照写


def test_show_prints_a_line_when_a_role_starts_and_when_it_ends(runs: Path) -> None:
    """--show（缺省）：观察者逐行播报角色开始与结束，结束行带时长（20.7 第 11 条）。"""
    code, output = _invoke("analyze", "--scenario", "clean", "--no-open")
    assert code == 0
    assert "technical#1 开始调用" in output and "完成，用时" in output
    assert "trader#1 开始调用" in output


def test_the_label_tables_cover_the_version_22_stages_and_gates() -> None:
    """_GATES 与 _STAGES 补上版本 2.2 的闸门与阶段（20.7 第 11 条）。"""
    assert {"intake", "ledger", "evidence", "audit", "tools"} <= set(cli_module._GATES)
    assert {"technical", "fundamentals", "disclosures", "news", "sentiment",
            "debate", "manager", "trader"} <= set(cli_module._STAGES)


# ---- resume：续跑（BLUEPRINT 20.7 第 8、9、10 条：缺陷 D3 的 app 一半、D8）----


class _CrashOn:
    """模型端口替身：第一次问到 crash_on 时抛普通异常（模拟进程被杀），之后照常回答。"""

    def __init__(self, replies: dict, crash_on: str) -> None:
        self._script, self._crash_on, self._armed = Script(replies), crash_on, True

    def for_role(self, role: Role) -> Model:
        if role.name == self._crash_on and self._armed:
            self._armed = False
            raise RuntimeError("模拟中断")
        return self._script.for_role(role)


def _crashed_run(settings: Settings, runs: Path) -> Path:
    """一个真的中断了的运行：demo 场景跑到交易员之前崩溃。日志没有 run.finished，检查点留在 manager。"""
    scenario = load_one("demo")
    job = wiring.prepare(settings, scenario="demo")
    run, deps = wiring.open_run(settings, job.request, scenario=scenario, flow=job.flow,
                                runs_dir=runs)
    run.models = _CrashOn(scenario.replies, "trader")
    with pytest.raises(RuntimeError):
        asyncio.run(wiring.run_flow(run, job.request, deps, job.flow))
    assert not [e for e in run.journal.events if e.type == "run.finished"]
    assert deps.out_dir is not None
    return deps.out_dir


def test_resume_carries_the_requests_already_spent(runs: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """D3 的 app 一半：续跑把已用请求数复原。上限 6、崩溃时已花 4 次：再调两次后第三次
    admit 就停机（calls_exhausted），而不是把上限当成从零开始（那会得出 ok）。"""
    settings = load(wiring.ROOT, force_offline=True, limits={"max_requests": 6})
    run_dir = _crashed_run(settings, runs)
    before = len((run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines())  # 续跑前的事件数
    scenario = load_one("demo")
    monkeypatch.setattr(resume_cmd, "_models", lambda s, e: Script(scenario.replies))
    outcome = asyncio.run(resume_cmd.resume_run(settings, run_dir / "journal.jsonl"))
    assert (outcome.status, outcome.reason) == ("delivered", "calls_exhausted")
    assert outcome.judgment == "not reviewed: calls_exhausted"
    text = (run_dir / "journal.jsonl").read_text(encoding="utf-8")
    journal = [json.loads(line) for line in text.splitlines()]
    called = [e for e in journal if e["type"] == "model.called"]
    assert len(called) == 6                                     # 4 次是崩溃前的，2 次是续跑新花的
    assert (run_dir / "trace.jsonl").exists()                   # 续跑也写 trace（20.6）
    trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    resumed = [json.loads(line) for line in trace if '"run.resumed"' in line]
    assert resumed and resumed[0]["after_seq"] == before        # after_seq 是续跑前日志的最后序号


def test_resume_truncates_a_torn_last_line(runs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D8：最后一行写了一半（没有换行、也解析不过）先截到完整行，说一行，然后照常续跑。

    续跑用的模型来源换成空剧本（问题 37：resume 的 _models 不看 IC_FORCE_OFFLINE，
    不换会在离线测试里对着 .env 的密钥发真请求）。"""
    settings = load(wiring.ROOT, force_offline=True)
    run_dir = _crashed_run(settings, runs)
    monkeypatch.setattr(resume_cmd, "_models",
                        lambda s, e: Script(load_one("demo").replies))
    journal = run_dir / "journal.jsonl"
    with journal.open("a", encoding="utf-8") as f:
        f.write('{"seq": 999, "type": "artifact.wri')           # 进程在追加中途被杀的残行
    code, output = _invoke("resume", str(run_dir))
    assert code == 0 and "已截" in output                        # 一行说明 + 照常续跑
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line) for line in lines)              # 剩下的每一行都完整


def test_resume_still_refuses_a_bad_line_in_the_middle(runs: Path) -> None:
    """D8 的另一半：坏行不在最后，就仍是错，不截、不续。"""
    settings = load(wiring.ROOT, force_offline=True)
    run_dir = _crashed_run(settings, runs)
    journal = run_dir / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines()
    lines[3] = "不是 JSON"
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, output = _invoke("resume", str(run_dir))
    assert code == 1 and "无法读取运行日志" in output and "已截" not in output


# ---- replay：未结束的运行（BLUEPRINT 20.7 第 13 条）----


def test_replay_says_an_unfinished_run_cannot_be_compared(runs: Path) -> None:
    """没有 run.finished 的日志：说明运行没有结束、无法比较，退出 1；不打印差异清单。"""
    settings = load(wiring.ROOT, force_offline=True)
    run_dir = _crashed_run(settings, runs)
    code, output = _invoke("replay", str(run_dir))
    assert code == 1 and "没有结束" in output and "不一致" not in output
