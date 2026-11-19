"""resume 命令：把一次被中断的运行从检查点接着跑完（BLUEPRINT 19.5 第 6 条）。

前提：运行目录里有 journal.jsonl（尚未 run.finished）与 checkpoints.sqlite。做法：
Journal.load 读旧事件；请求、流程与设置按 run.started 复原（与 replay 相同的规则）；
黑板从旧事件折叠且保持可写；预算把已发生的每次模型调用重放进去（这样续跑结束时的
run/usage 与一次 uninterrupted 运行相同）；模型、上限、输出方式同样复原。
然后重算运行签名，与 run.started 记录的不一致就拒绝；一致则以该签名为线程 ID，
用 SQLite 检查点 drive(resume=True) 继续图，收尾与 analyze 相同。

每个节点在自己的末尾提交事件（决策 42），所以崩溃后的日志恰好包含已完成的节点，
检查点与日志一致；被中断的那个调用花掉的部分不在日志里，也不在预算里。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..domain.types import Outcome
from ..flows.analyze import FlowConfig
from ..flows.analyze import drive as drive_flow
from ..kernel.board import Board
from ..kernel.budget import Budget, Ceiling
from ..kernel.events import Event, Journal
from ..kernel.runtime import Limits, Run, RunUsage  # RunUsage 经内核重导出：预算重放需要该类型
from .replay import _one_line  # 同一层的复用：压缩异常文本为一行
from .settings import LimitsSection, RunSection, Settings
from .wiring import ROOT, BadRequest, build_deps, checkpoint, run_signature, write_page


class _ResumeJournal(Journal):
    """接着已有日志写：先装入旧事件（序号接着编），新事件写回同一个文件。"""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._events = list(Journal.load(path))
        self._path = path


class _ResumingBoard(Board):
    """从旧事件折叠出黑板，同时保持可写：新写入进入续用的日志。"""

    def __init__(self, journal: _ResumeJournal, events: tuple[Event, ...]) -> None:
        super().__init__(journal)
        for event in events:
            self._apply(event)


def _steps_used(events: tuple[Event, ...]) -> dict[str, int]:
    """日志里已经用过的步骤计数（"trader#3" -> {"trader": 3}），新 step_id 接着编。"""
    steps: dict[str, int] = {}
    for event in events:
        if "#" in event.step:
            name, _, n = event.step.rpartition("#")
            if n.isdigit():
                steps[name] = max(steps.get(name, 0), int(n))
    return steps


class _ResumingRun(Run):
    """续跑的 Run：把已用过的步骤计数一并复原，被中断角色之后的 step_id 不会重号。"""

    def __init__(self, *, steps: dict[str, int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._steps = dict(steps)


def run(settings: Settings, run_dir: str) -> int:
    """把 RUN_DIR 里被中断的运行从检查点续跑完。已结束或签名不符的运行拒绝续跑。"""
    path = _journal_path(run_dir)
    if path is None:
        print(f"找不到运行日志：{run_dir}（应是一个包含 journal.jsonl 的运行目录）")
        return 1
    try:
        events = Journal.load(path)
    except (OSError, ValidationError) as e:
        print(f"无法读取运行日志 {path}：{_one_line(str(e))}")
        return 1
    started = next((e for e in events if e.type == "run.started"), None)
    if started is None:
        print(f"运行日志 {path} 里没有 run.started 事件，无法续跑")
        return 1
    if any(e.type == "run.finished" for e in events):
        print("该运行已经结束，无需续跑。")
        return 1
    try:
        flow = FlowConfig(**(started.data.get("flow") or {}))
    except ValidationError as e:
        print(f"run.started 里记录的设置无法复原：{_one_line(str(e))}")
        return 1

    board = Board.fold(events)
    request = dict(started.data.get("request") or {})
    if not request.get("as_of") and "request/scope" in board.snapshot():
        request["as_of"] = board.snapshot()["request/scope"]["as_of"]
    restored = _restored(started.data, settings)

    try:
        deps = build_deps(restored)
    except OSError as e:
        print(f"数据集 {restored.run.universe!r} 读不出来：{e}")
        return 1

    recorded_thread = started.data.get("thread")
    recomputed = run_signature(request, deps, flow,
                               dict(started.data.get("models") or {}))
    if recorded_thread is None:
        print("运行日志里没有运行签名（版本 2.2 之前的运行），无法续跑。")
        return 1
    if recomputed != recorded_thread:
        print(f"运行签名不一致：日志记录 {recorded_thread}，现在重算 {recomputed}。"
              "代码、日期、流程配置、档位或组合与原运行不同，拒绝续跑。")
        return 1

    journal = _ResumeJournal(path)
    run_ctx = _ResumingRun(id=path.parent.name, journal=journal,
                           board=_ResumingBoard(journal, events),
                           models=_models(restored, started), budget=_budget(started.data, events),
                           limits=Limits(**_limits(started.data, restored)),
                           output_mode=restored.run.output_mode,
                           steps=_steps_used(events))

    async def _continue() -> Outcome:
        async with checkpoint(path.parent) as saver:
            return await drive_flow(run_ctx, request, deps, flow, checkpointer=saver,
                                    thread_id=recomputed, resume=True)

    try:
        outcome = asyncio.run(_continue())
    except BadRequest as e:                       # drive 不抛这个；留作将来 prepare 级检查
        print(f"无法续跑：{e}")
        return 1

    write_page(run_ctx, deps)
    print(_summary(outcome, path.parent))
    return 0


def _summary(outcome: Outcome, run_dir: Path) -> str:
    return (f"续跑完成：{outcome.status}，原因 {outcome.reason}，"
            f"判断 {outcome.judgment}。运行目录：{run_dir}")


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
    """run.started 记录了什么就复原什么，其余沿用当前设置（与 replay 相同的规则）。"""
    updates: dict[str, Any] = {
        "run": RunSection(universe=data.get("universe") or settings.run.universe,
                          output_mode=data.get("output_mode") or settings.run.output_mode)}
    if data.get("limits"):
        updates["limits"] = LimitsSection(**data["limits"])
    ceilings = data.get("ceilings") or {}
    updates["budget"] = settings.budget.model_copy(update={
        "run_tokens": ceilings.get("run_tokens") or 0,
        "run_cost": ceilings.get("run_cost") or 0.0,
        "roles": dict(ceilings.get("roles") or {})})
    return settings.model_copy(update=updates)


def _limits(data: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """实际生效的请求/时长/超时上限：日志记录优先，缺省沿用当前设置。"""
    recorded = data.get("limits") or {}
    limits = settings.limits
    return {"max_requests": recorded.get("max_requests", limits.max_requests),
            "max_seconds": recorded.get("max_seconds", limits.max_seconds),
            "request_timeout_s": recorded.get("request_timeout_s", limits.request_timeout_s)}


def _budget(data: dict[str, Any], events: tuple[Event, ...]) -> Budget:
    """按 run.started 的上限重建预算，并把已发生的每次模型调用重放进去。"""
    ceilings = data.get("ceilings") or {}
    budget = Budget(Ceiling(tokens=ceilings.get("run_tokens") or None,
                            cost=ceilings.get("run_cost") or None),
                    groups=dict(ceilings.get("roles") or {}), name="run")
    for event in events:
        if event.type != "model.called":
            continue
        usage = event.data.get("usage") or {}
        budget.record(event.actor, event.data.get("model", ""),
                      RunUsage(requests=usage.get("requests", 0),
                               input_tokens=usage.get("input_tokens", 0),
                               output_tokens=usage.get("output_tokens", 0)),
                      event.data.get("cost"))
    return budget


def _models(settings: Settings, started: Event) -> Any:
    """续跑用真实模型的档位（原运行若是脚本化的，续跑时没有剧本，按当前设置装配）。"""
    from ..adapters.models import Tiers, build  # 局部引入避免加载顺序问题
    from ..adapters.scripted import Script

    key = settings.api_key.get_secret_value() if settings.api_key else None
    try:
        return Tiers(build(settings.tiers.quick, base_url=settings.base_url, api_key=key),
                     build(settings.tiers.deep, base_url=settings.base_url, api_key=key))
    except (RuntimeError, ValueError):
        return Script({})          # 没有可用模型：续跑会在第一次调用处以 model_unavailable 停下
