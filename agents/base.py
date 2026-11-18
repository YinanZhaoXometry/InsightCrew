"""Agent 基类。所有角色共享的只有三件事：模型、总线、黑板。

刻意保持极薄——角色之间的差异必须体现在**prompt、可见信息、可用工具**上，
而不是体现在代码结构上。这正是"角色专精"可被检验的地方。
"""
from __future__ import annotations

from core.messages import Intent


class Agent:
    name = "Agent"
    role = ""

    def __init__(self, llm, bus, bb):
        self.llm = llm
        self.bus = bus
        self.bb = bb

    def log(self, to: str, intent: Intent, note: str, payload: dict | None = None,
            refs: list | None = None):
        return self.bus.send(self.name, to, intent, payload or {}, refs or [], note)
