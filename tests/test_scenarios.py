"""场景测试：每个场景文件给出预期的结果与调用序列，实际运行必须一致。"""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

import pytest

from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT, open_run
from insightcrew.evals.scenarios import load_all, names, run_one
from insightcrew.flows.analyze import analyze

# 模块级装载：文件不合契约会在收集阶段就失败。
SETTINGS: Settings = load(ROOT, force_offline=True)        # .env 里有密钥时也不构造真实模型
SCENARIOS = load_all()


def test_scenario_files_exist_and_are_declared() -> None:
    assert {s.name for s in SCENARIOS} == set(names())
    assert len(SCENARIOS) >= 14            # BLUEPRINT 12.2 的清单


@pytest.mark.parametrize("name", names())
def test_scenario_matches_its_expectation(name: str, tmp_path: Path) -> None:
    scenario = next(s for s in SCENARIOS if s.name == name)
    opener = partial(open_run, SETTINGS, runs_dir=tmp_path)       # 测试的运行不进仓库的 runs/
    result = asyncio.run(run_one(scenario, opener, analyze))
    assert result.ok, f"{name}：{result.detail}"


def test_the_channel_scenarios_expect_what_blueprint_1813_states() -> None:
    """18.13 的表：五个新场景的调用数与预期（channels_clean 9、uncovered 8、quiet 7）。"""
    by_name = {s.name: s for s in SCENARIOS}
    assert len(by_name) == 36      # 21 个 2.0/2.1 + v2.2 的 13 个（WP32–WP35）+ WP52 的 2 个审批门场景
    assert len(by_name["channels_clean"].expect.calls) == 9
    assert len(by_name["uncovered"].expect.calls) == 8
    assert len(by_name["quiet"].expect.calls) == 7
    assert by_name["quiet"].request["as_of"] == "2026-06-30"
    assert by_name["uncovered"].request["ticker"] == "0000.HK"
    for name in ("channels_clean", "misquote_fundamentals", "ghost_item", "uncovered", "quiet"):
        assert by_name[name].config.flow.channels == ("fundamentals", "disclosures")
        assert by_name[name].expect.status == "delivered"
    assert "fundamentals" in by_name["misquote_fundamentals"].expect.calls
    assert "disclosures" in by_name["ghost_item"].expect.calls
