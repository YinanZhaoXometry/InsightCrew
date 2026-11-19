"""WP37 的一致性评测测试：版本 2.2 的两个新块（BLUEPRINT 19.16）。

合成数据上两块全一致（点数钉死：工具 7 个家族 × 3 个分析日 × 每家族 3 个窗口 = 63
次调用；宏观 4 条序列 × 3 个变换 × 8 个分析日 = 96 个数据点）；再各伪造一处
分歧（工具谎称数据不可用而 recompute 算得出来；macro_sql 被篡改），块必须报出。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from insightcrew.evals import consistency
from insightcrew.flows.tools import ToolBox

ROOT = Path(__file__).resolve().parent.parent


def test_the_tools_block_agrees_on_both_paths() -> None:
    problems: list[str] = []
    assert consistency._tools_block(ROOT, problems) == 63
    assert problems == []


def test_the_macro_block_agrees_on_both_paths() -> None:
    problems: list[str] = []
    assert consistency._macro_block(ROOT, problems) == 96
    assert problems == []


def test_a_tool_that_denies_data_recompute_can_produce_is_reported(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """被篡改的工具一律说历史不足：domain/recompute 算得出来时必须报不一致。"""

    def fake_build_tools(run: object, deps: object, specs: object) -> ToolBox:
        box = ToolBox([])

        def indicator(name: str, n: int) -> str:
            return f"{name} 的窗口 n = {n} 历史不足。数据不可用，不要编造。"

        box["indicator"] = indicator
        return box

    monkeypatch.setattr(consistency, "build_tools", fake_build_tools)
    problems: list[str] = []
    consistency._tools_block(ROOT, problems)
    assert any("工具说数据不可用" in p for p in problems)


def test_a_disagreement_between_the_macro_paths_is_reported(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """被篡改的路径 2 永远报 123.456：level 与路径 1 不再一致，必须逐日报出。"""

    def wrong_level(db: Any, *, month_end: Any, as_of: Any) -> float:
        return 123.456

    monkeypatch.setattr(consistency.macro_sql, "level", wrong_level)
    problems: list[str] = []
    consistency._macro_block(ROOT, problems)
    assert problems and problems[0].startswith("宏观 ")
