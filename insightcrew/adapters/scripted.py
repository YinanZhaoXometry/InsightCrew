"""两个不需要网络的模型来源，都建立在 Pydantic AI 的 FunctionModel 上。

Script：按角色、按请求次序返回预先写好的回答（离线演示与场景测试）。
Replay：从一份事件日志里取回当时的真实回复，原样重放（任何一次运行都可重放）。
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic_ai import (
    ModelAPIError,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from ..kernel.events import Event
from ..kernel.runtime import Role


class Script:
    """replies: 角色名 -> 回答列表。每个回答是一个符合该角色契约的 JSON 对象（或一段原样返回的文字）。"""

    def __init__(self, replies: Mapping[str, list[Any]], *, name: str = "scripted",
                 input_tokens: int = 1000, output_tokens: int = 300):
        self._replies, self._name = replies, name
        self._usage = RequestUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        self._count: dict[str, int] = {}

    def for_role(self, role: Role) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            queue = self._replies.get(role.name)
            if not queue:
                raise ModelAPIError(self._name, f"剧本里没有角色 {role.name!r} 的回答")
            i = self._count.get(role.name, 0)
            self._count[role.name] = i + 1
            reply = queue[min(i, len(queue) - 1)]             # 用完后重复最后一条
            if info.output_tools and not isinstance(reply, str):
                part: TextPart | ToolCallPart = ToolCallPart(info.output_tools[0].name, reply)
            else:
                part = TextPart(reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))
            return ModelResponse(parts=[part], usage=self._usage, model_name=self._name)

        return FunctionModel(respond, model_name=self._name)


class Replay:
    """按角色、按次序重放日志里记录的回复。提示词与录制时不同就报错，而不是给出一个对不上的回答。"""

    def __init__(self, events: Iterable[Event]):
        self._calls: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            if e.type == "model.called":
                self._calls.setdefault(e.actor, []).append(e.data)
        self._pending: dict[str, list[ModelResponse]] = {}

    def for_role(self, role: Role) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            pending = self._pending.setdefault(role.name, [])
            if not pending:                                   # 这个角色的下一次调用开始
                calls = self._calls.get(role.name)
                if not calls:
                    raise ModelAPIError("replay", f"回放未命中：日志里没有角色 {role.name!r} 的下一次调用")
                call = calls.pop(0)
                if _prompt(messages) != call["prompt"]:
                    raise ModelAPIError("replay", f"回放未命中：角色 {role.name!r} 的提示词与录制时不同")
                recorded = ModelMessagesTypeAdapter.validate_python(call["messages"])
                pending.extend(m for m in recorded if isinstance(m, ModelResponse))
            return pending.pop(0)

        calls = self._calls.get(role.name)
        return FunctionModel(respond, model_name=calls[0]["model"] if calls else "replay")


def _prompt(messages: list[ModelMessage]) -> str:
    first = messages[0]
    assert isinstance(first, ModelRequest)
    return "".join(p.content for p in first.parts
                   if isinstance(p, UserPromptPart) and isinstance(p.content, str))
