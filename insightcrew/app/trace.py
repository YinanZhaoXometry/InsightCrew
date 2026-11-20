"""计时与活动轨迹：一次运行旁边的 trace.jsonl（BLUEPRINT 20.6，决策 52）。

它只是测量。没有角色读它，report.md 里的数字没有一个来自它，replay 与 resume 不读
它的内容，删掉它不改变任何结果（不变量 22，tests/test_trace.py 逐条核对日志相等）。
每行一个 JSON 对象：ts（UTC ISO、毫秒）、kind，然后该类事件的字段：

    role.started   step, role, tier, model          ← 内核观察者钩子（20.5）
    role.finished  step, role, ms, ok, requests, input_tokens, output_tokens, tool_calls
    role.failed    step, role, ms                   ← 内核观察者钩子
    run.resumed    after_seq                        ← app/resume.py
    run.cancelled  —                                ← app/server.py（WP45）

接线位置：wiring.run_flow 与续跑路径把它挂成 run.observe，也就是只有真实运行
（命令行与网页）写它；场景、测试与 replay 直接调 flows.analyze.analyze，不写。
一条 role.started 没有收尾，就是一次被中断的调用。只用标准库。
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_Now = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


class Tracer:
    """观察者钩子的落盘端：`(kind, data)` 一次调用追加一行，锁内写互斥。"""

    def __init__(self, path: Path, now: _Now | None = None) -> None:
        self._path, self._now = path, now or _now
        self._lock = threading.Lock()

    def __call__(self, kind: str, data: Mapping[str, Any]) -> None:
        line = {"ts": self._now().isoformat(timespec="milliseconds"), "kind": kind, **data}
        with self._lock:
            _append(self._path, line)


def mark(path: Path, kind: str, **data: Any) -> None:
    """应用自己写的那两行（run.resumed、run.cancelled）：不经过 Tracer 实例的一次落盘。"""
    _append(path, {"ts": _now().isoformat(timespec="milliseconds"), "kind": kind, **data})


def _append(path: Path, line: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def load(path: Path) -> list[dict[str, Any]]:
    """读一份 trace：解析不过的行跳过，文件不存在返回空列表（测量不参与任何判断）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = []
    for raw in text.splitlines():
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            lines.append(parsed)
    return lines
