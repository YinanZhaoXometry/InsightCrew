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
