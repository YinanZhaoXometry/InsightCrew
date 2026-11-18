"""事件日志：一次运行的唯一事实来源。只追加，不修改。

黑板、报告、运行页面、成本表、评测，全部是这份日志的投影。
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "run.started", "artifact.written", "model.called", "model.failed",
    "gate.checked", "route", "budget.exceeded", "run.finished",
]


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int                                  # 运行内从 1 递增，由 Journal 分配
    ts: str                                   # ISO 时间，来自注入的时钟；不参与任何业务判断
    type: EventType                           # 只有这八种；新增一种就是改内核
    step: str = ""                            # 步骤 ID，例如 "trader#2"
    actor: str = ""                           # 角色名或代码阶段名
    data: dict[str, Any] = Field(default_factory=dict)
    caused_by: tuple[int, ...] = ()           # 这条事件依赖的事件序号


class Journal:
    def __init__(self, path: Path | None = None, now: Callable[[], str] = lambda: ""):
        self._path, self._now = path, now
        self._events: list[Event] = []
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def append(self, type: EventType, *, step: str = "", actor: str = "",
               data: dict[str, Any] | None = None, caused_by: Iterable[int] = ()) -> Event:
        with self._lock:
            event = Event(seq=len(self._events) + 1, ts=self._now(), type=type, step=step,
                          actor=actor, data=data or {}, caused_by=tuple(caused_by))
            line = event.model_dump_json()    # 先序列化：写不进日志的事件不算发生过
            self._events.append(event)
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            return event

    @property
    def events(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._events)

    @staticmethod
    def load(path: Path) -> tuple[Event, ...]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return tuple(Event.model_validate_json(line) for line in lines if line)
