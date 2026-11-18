"""黑板：事件日志中 artifact.written 事件的折叠结果。

黑板里只存纯数据（JSON）。类型只出现在每一步的边界：put 时序列化，get 时校验。
键的形式是 "<所有者>/<名称>"，每次写入产生一个新版本，旧版本保留。
"""
from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from .events import Event, Journal


class Board:
    def __init__(self, journal: Journal | None = None):
        self._journal = journal
        self._versions: dict[str, list[tuple[int, Any]]] = {}    # key -> [(seq, value)]

    # ---- 写 ----
    def put(self, key: str, value: Any, *, by: str, step: str = "",
            caused_by: Iterable[int] = ()) -> int:
        if self._journal is None:
            raise RuntimeError("只读黑板（由日志折叠而来）不能写入")
        if "/" not in key:
            raise ValueError(f"键必须形如 '<所有者>/<名称>'：{key!r}")
        data = to_jsonable_python(value)
        version = len(self._versions.get(key, ())) + 1
        event = self._journal.append("artifact.written", step=step, actor=by, caused_by=caused_by,
                                     data={"key": key, "version": version, "value": data})
        self._apply(event)
        return event.seq

    def _apply(self, event: Event) -> None:
        if event.type == "artifact.written":
            self._versions.setdefault(event.data["key"], []).append((event.seq, event.data["value"]))

    # ---- 读 ----
    def has(self, key: str) -> bool:
        return key in self._versions

    def get(self, key: str, tp: Any = None) -> Any:
        """最新版本。给了 tp 就校验成该类型（例如 Thesis、list[Fact]），否则返回纯数据。键不存在抛 KeyError。"""
        value = self._versions[key][-1][1]
        return value if tp is None else TypeAdapter(tp).validate_python(value)

    def seq(self, key: str) -> int:
        """最新版本对应的事件序号，用来填写 caused_by。"""
        return self._versions[key][-1][0]

    def history(self, key: str) -> list[Any]:
        return [v for _, v in self._versions.get(key, [])]

    def view(self, patterns: Iterable[str]) -> dict[str, Any]:
        """一个角色能看到的全部内容：匹配到的键的最新值，按声明顺序排列。

        这是黑板内容进入提示词的唯一通道（L1 上下文隔离靠它保证）。
        """
        out: dict[str, Any] = {}
        for pattern in patterns:
            for key in sorted(self._versions):
                if fnmatchcase(key, pattern) and key not in out:
                    out[key] = self._versions[key][-1][1]
        return out

    def snapshot(self) -> dict[str, Any]:
        return {k: v[-1][1] for k, v in sorted(self._versions.items())}

    # ---- 折叠 ----
    @classmethod
    def fold(cls, events: Iterable[Event]) -> Board:
        board = cls(None)
        for event in events:
            board._apply(event)
        return board
