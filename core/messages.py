"""消息总线：智能体之间唯一的通信通道。

为什么要结构化消息，而不是让 agent 自由对话：
    1) intent 是有限枚举 -> 协作过程可被状态机验证，不会发散；
    2) payload 是 JSON -> 可测试、可断言，出问题能定位到具体字段；
    3) 每条消息落盘 trace.jsonl -> 演示时可回放，答辩时可举证。

教学要点：
    自然语言"寒暄式通信"（"我做完了，请查收"）是伪多智能体的典型特征——
    它既不能被程序校验，也不能被评委检验。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    PROFILE = "PROFILE"            # 数据画像
    PLAN = "PLAN"                  # 产出/修订分析计划
    EXECUTE_STEP = "EXECUTE_STEP"  # 执行一个分析步骤
    CRITIQUE = "CRITIQUE"          # 审计结论
    REVISE = "REVISE"              # 驳回，要求重做
    FINALIZE = "FINALIZE"          # 出报告
    STOP = "STOP"                  # 终止（预算耗尽/停滞）


@dataclass
class Msg:
    sender: str
    to: str
    intent: str
    payload: dict = field(default_factory=dict)
    refs: list = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"sender": self.sender, "to": self.to, "intent": self.intent,
                "payload": self.payload, "refs": self.refs, "note": self.note}


class MessageBus:
    """写消息 + 记事件 + 落盘。

    events 是给前端时间线用的（比 messages 更粗，但更适合渲染）。
    """

    def __init__(self, run_dir: str | None = None, verbose: bool = True):
        self.run_dir = run_dir
        self.verbose = verbose
        self.messages: list[Msg] = []
        self.events: list[dict] = []
        self._fh = None
        if run_dir:
            os.makedirs(run_dir, exist_ok=True)
            self._fh = open(os.path.join(run_dir, "trace.jsonl"), "a", encoding="utf-8")

    # ---- 智能体之间的消息 ----
    def send(self, sender: str, to: str, intent, payload: dict | None = None,
             refs: list | None = None, note: str = "") -> Msg:
        m = Msg(sender, to, intent.value if isinstance(intent, Intent) else str(intent),
                payload or {}, refs or [], note)
        self.messages.append(m)
        self._emit({"kind": "message", "sender": m.sender, "to": m.to,
                    "intent": m.intent, "note": m.note, "payload": m.payload})
        if self.verbose:
            print(f"[{m.sender:>9} -> {m.to:<9}] {m.intent:<12} {m.note}")
        return m

    # ---- 系统级事件（工具调用、判定、状态）----
    def event(self, kind: str, **kw):
        self._emit({"kind": kind, **kw})
        if self.verbose:
            head = {"tool": "  · 工具", "verdict": "  ! 判定",
                    "state": "  * 状态", "budget": "  * 预算"}.get(kind, f"  · {kind}")
            print(f"{head}: {kw.get('summary', '')}")

    def _emit(self, ev: dict):
        ev.setdefault("t", time.strftime("%H:%M:%S"))
        ev["ts"] = time.time()
        self.events.append(ev)
        if self._fh:
            self._fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
