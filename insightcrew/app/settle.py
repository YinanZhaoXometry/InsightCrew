"""settle 命令（BLUEPRINT 19.13）：结算到 known_by 为止持有期已走完的决策，
让复盘员给每条新结果写一条教训。每个结果打印一行中文；分歧与不可结算的决策
照常汇报但不改变退出码（它们保持待决，等下次结算）。
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from ..adapters.decision_log import DecisionLog
from ..flows.settle import settle as settle_flow
from ..kernel.runtime import Limits
from .settings import Settings
from .wiring import ROOT, _models, build_deps, today


def run(settings: Settings, known_by: str, *, settle_root: Path | None = None) -> int:
    """命令入口。known_by 缺省用今天；日期无法解析时给一行中文并退出码 2。"""
    if known_by:
        try:
            day = date.fromisoformat(known_by)
        except ValueError:
            print(f"日期 {known_by!r} 无法解析：请写成 YYYY-MM-DD，例如 2026-11-20")
            return 2
    else:
        day = today(settings)
    return asyncio.run(_settle(settings, day, settle_root or ROOT / "runs" / "memory" / "settle"))


async def _settle(settings: Settings, known_by: date, settle_root: Path) -> int:
    deps = build_deps(settings)
    log_path = ROOT / settings.memory.path
    log_path.parent.mkdir(parents=True, exist_ok=True)   # DecisionLog 的锁文件先于 _write 建目录
    log = DecisionLog(log_path, max_entries=settings.memory.max_entries)
    models, mode, _names = _models(settings, None, None)
    pending = len(log.pending())
    print(f"结算到 {known_by}：{pending} 条待决决策（模型：{mode}）")
    outcomes = await settle_flow(
        deps, log, known_by, holding_days=settings.memory.holding_days, models=models,
        runs_root=settle_root,
        limits=Limits(max_requests=settings.limits.max_requests,
                      max_seconds=settings.limits.max_seconds,
                      request_timeout_s=settings.limits.request_timeout_s),
        output_mode=settings.run.output_mode, note=print)
    print(f"新落账 {len(outcomes)} 条结果，剩余待决 {len(log.pending())} 条；"
          f"复盘运行目录：{settle_root}")
    return 0
