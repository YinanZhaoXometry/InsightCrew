"""预算账本：token 与成本的唯一记账处。

用量和价格来自 Pydantic AI（每次回复自带用量，价格由 genai-prices 提供）；
这里只放本项目自己的策略：
    - 两种上限：token（输入+输出累计）与成本；None 表示不设限。
    - 三层范围：批次 > 单次运行 > 角色组。内层只能收紧，取较低者。
    - 调用前检查；严格大于才算超限；因此最多超出一次调用，且那次调用的结果保留。
    - 重试、辩论、评审全部计入同一个运行预算。
    - 设了成本上限却查不到该模型的价格，是配置错误，在第一次记账时报出。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from pydantic_ai.usage import RunUsage


@dataclass(frozen=True, slots=True)
class Ceiling:
    tokens: int | None = None
    cost: float | None = None


class BudgetConfigError(ValueError):
    pass


class Budget:
    def __init__(self, ceiling: Ceiling | None = None, *, groups: dict[str, int] | None = None,
                 parent: Budget | None = None, name: str = "run"):
        self.name, self.ceiling, self.parent = name, ceiling or Ceiling(), parent
        self._groups = dict(groups or {})
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}   # (role, model) -> 统计行
        self.tokens, self.cost = 0, 0.0

    def exceeded(self, group: str = "") -> str | None:
        """返回已超限的范围名（"batch" / "run" / "role:<组>"），未超限返回 None。"""
        if self.parent is not None and (scope := self.parent.exceeded()):
            return scope
        with self._lock:
            if self.ceiling.tokens is not None and self.tokens > self.ceiling.tokens:
                return self.name
            if self.ceiling.cost is not None and self.cost > self.ceiling.cost:
                return self.name
            limit = self._groups.get(group)
            if limit is not None and self._group_tokens(group) > limit:
                return f"role:{group}"
        return None

    def _group_tokens(self, group: str) -> int:
        return sum(r["input_tokens"] + r["output_tokens"]
                   for (role, _), r in self._rows.items() if role.split(".")[0] == group)

    def record(self, role: str, model: str, usage: RunUsage, cost: float | None) -> None:
        """记一次角色调用（可能含一次纠正重试）。cost 为 None 表示这个模型查不到价格。"""
        if cost is None and self.ceiling.cost is not None:
            raise BudgetConfigError(f"设置了成本上限，但查不到模型 {model!r} 的价格")
        with self._lock:
            row = self._rows.setdefault((role, model), {
                "role": role, "model": model, "calls": 0, "requests": 0,
                "input_tokens": 0, "output_tokens": 0, "cost": None})
            row["calls"] += 1
            row["requests"] += usage.requests
            row["input_tokens"] += usage.input_tokens
            row["output_tokens"] += usage.output_tokens
            if cost is not None:
                row["cost"] = (row["cost"] or 0.0) + cost
                self.cost += cost
            self.tokens += usage.input_tokens + usage.output_tokens
        if self.parent is not None:
            self.parent.record(role, model, usage, cost)

    def table(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for _, r in sorted(self._rows.items())]
