"""记忆阶段（BLUEPRINT 19.13）。WP31 留桩：签名即最终签名，WP35 填正文。

lessons 在准入时运行（memory 打开才有），record 在运行结束、结果为 delivered 之后运行。
"""
from __future__ import annotations

from typing import Any

from ..flows.deps import Deps
from ..kernel.runtime import Run


def lessons(run: Run, deps: Deps) -> None:
    """WP35 填写：select_lessons 从决策日志选课，只看分析日已知结果的记录（不变量 20）。
    桩写空课程与 none_known 状态（19.8 第 4 条的显式缺席）。"""
    run.board.put("memory/lessons", [], by="memory")
    run.board.put("memory/status", _none(), by="memory")


def record(run: Run, deps: Deps) -> None:
    """WP35 填写：交付的运行追加一条 DecisionRecord。重放时日志端口是按日志供给的替身，
    append 什么都不写（决策 44），所以这里不需要知道自己是不是重放。"""
    return None


def _none() -> dict[str, Any]:
    return {"state": "none_known", "reason": "记忆尚未接入（WP35 的桩）"}
