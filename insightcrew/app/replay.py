"""replay 命令：从一份事件日志无模型重放一次运行，核对结果是否可复现。WP5 留空，WP6 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。

做法（BLUEPRINT 13 WP6）：Journal.load 读日志；请求与设置取自 run.started 事件——
流程配置按日志复原（open_run 的 flow 参数就是为此准备的），output_mode、universe、
limits 与运行层预算上限这些 run.started 里记录了的也一并复原，日志里没记录的
（档位名、批次预算、角色组上限、IC_TODAY）沿用当前设置。然后 open_run 以 replay=事件
装配（Replay 模型按次序原样奉还当时的回复，不联网），analyze 用 asyncio.run 跑完，
把结果四项（status、reason、facts_and_rules、judgment）与黑板快照同原运行
（Board.fold 折叠原日志）逐一比较：一致打印"一致"退出 0，否则打印差异摘要退出 1。

重放这次运行由 open_run 另开一个新的运行目录（runs/ 下），这是装配的固有行为，属正常。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..domain.types import Outcome
from ..flows.analyze import FlowConfig
from ..flows.analyze import analyze as run_flow
from ..kernel.board import Board
from ..kernel.events import Journal
from .settings import LimitsSection, RunSection, Settings
from .wiring import ROOT, open_run

_FIELDS = ("status", "reason", "facts_and_rules", "judgment")


def run(settings: Settings, run_dir: str) -> int:
    """按 run.started 里的请求与设置重放，比较结果与黑板快照是否一致。"""
    path = _journal_path(run_dir)
    if path is None:
        print(f"找不到运行日志：{run_dir}（应是一个包含 journal.jsonl 的运行目录，"
              "如 runs/<运行名> 或直接给 <运行名>）")
        return 1
    try:
        events = Journal.load(path)
    except (OSError, ValidationError) as e:
        print(f"无法读取运行日志 {path}：{_one_line(str(e))}")
        return 1
    started = next((e for e in events if e.type == "run.started"), None)
    if started is None:
        print(f"运行日志 {path} 里没有 run.started 事件，无法重放")
        return 1

    request = dict(started.data.get("request") or {})
    try:
        flow = FlowConfig(**(started.data.get("flow") or {}))
        replayed_settings = _restored(started.data, settings)
    except ValidationError as e:
        print(f"run.started 里记录的设置无法复原：{_one_line(str(e))}")
        return 1

    run, deps = open_run(replayed_settings, request, replay=events, flow=flow)
    outcome = asyncio.run(run_flow(run, request, deps, flow))

    problems = _diff(Board.fold(events).snapshot(), run.board.snapshot(), outcome)
    if problems:
        print("不一致：")
        for problem in problems:
            print(f"  {problem}")
    else:
        print(f"一致：结果（{outcome.status}，原因 {outcome.reason}，判断 {outcome.judgment}）"
              f"与黑板快照完全相同")
    print(f"重放运行目录：{deps.out_dir}")
    return 1 if problems else 0


def _journal_path(run_dir: str) -> Path | None:
    """run_dir 可以是绝对路径、相对路径（runs/<运行名>）、裸 <运行名>，或直接指到 journal.jsonl。"""
    given = Path(run_dir)
    bases = [given] if given.is_absolute() else [ROOT / given, ROOT / "runs" / given,
                                                 Path.cwd() / given]
    for base in bases:
        path = base if base.name == "journal.jsonl" else base / "journal.jsonl"
        if path.is_file():
            return path
    return None


def _restored(data: dict[str, Any], settings: Settings) -> Settings:
    """run.started 记录了什么就复原什么，其余沿用当前设置，得到重放要用的设置。"""
    updates: dict[str, Any] = {
        "run": RunSection(universe=data.get("universe") or settings.run.universe,
                          output_mode=data.get("output_mode") or settings.run.output_mode)}
    if data.get("limits"):
        updates["limits"] = LimitsSection(**data["limits"])
    ceilings = data.get("ceilings") or {}
    updates["budget"] = settings.budget.model_copy(update={
        "run_tokens": ceilings.get("run_tokens") or 0,
        "run_cost": ceilings.get("run_cost") or 0.0})
    return settings.model_copy(update=updates)


def _diff(before: dict[str, Any], after: dict[str, Any], outcome: Outcome) -> list[str]:
    """结果四项与黑板快照的差异；空列表表示一致。"""
    problems: list[str] = []
    original = before.get("run/outcome") or {}
    for field in _FIELDS:
        replayed = getattr(outcome, field)
        if original.get(field) != replayed:
            problems.append(f"结果 {field}：原 {original.get(field)!r}，重放 {replayed!r}")
    for key in sorted(set(before) | set(after)):
        if key not in after:
            problems.append(f"黑板 {key}：重放缺失")
        elif key not in before:
            problems.append(f"黑板 {key}：重放多出")
        elif before[key] != after[key]:
            problems.append(f"黑板 {key}：内容不同")
    return problems


def _one_line(text: str) -> str:
    """把异常文本压成一行，保证提示的"一行"名副其实。"""
    return " ".join(text.split())
