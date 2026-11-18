"""场景测试：每个场景文件给出预期的结果与调用序列，实际运行必须一致。"""
from __future__ import annotations

import asyncio
from functools import partial

import pytest

from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT, open_run
from insightcrew.evals.scenarios import load_all, names, run_one
from insightcrew.flows.analyze import analyze

# 模块级装载：文件不合契约会在收集阶段就失败。
SETTINGS: Settings = load(ROOT)
SCENARIOS = load_all()


def test_scenario_files_exist_and_are_declared() -> None:
    assert {s.name for s in SCENARIOS} == set(names())
    assert len(SCENARIOS) >= 14            # BLUEPRINT 12.2 的清单


@pytest.mark.parametrize("name", names())
def test_scenario_matches_its_expectation(name: str) -> None:
    scenario = next(s for s in SCENARIOS if s.name == name)
    result = asyncio.run(run_one(scenario, partial(open_run, SETTINGS), analyze))
    assert result.ok, f"{name}：{result.detail}"
