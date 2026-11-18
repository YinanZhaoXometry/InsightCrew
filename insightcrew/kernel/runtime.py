"""一次运行的上下文，以及角色执行器。

run_role 是系统里唯一运行智能体的地方。它做且只做五件事：
    取视图 -> 渲染提示词 -> 过闸门（次数、时间、预算）-> 运行智能体并记账 -> 写黑板

模型调用、结构化输出、输出不合契约时的一次纠正重试、各家模型的能力差异，
都交给 Pydantic AI 的 Agent；这里不重复实现。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from pydantic_ai import (
    Agent,
    ModelAPIError,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ModelSettings,
    NativeOutput,
    PromptedOutput,
    ToolOutput,
    UnexpectedModelBehavior,
    UserError,
    capture_run_messages,
)
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .board import Board
from .budget import Budget
from .events import Journal

OutputMode = Literal["auto", "native", "tool", "prompted"]


class Halt(Exception):
    """运行必须停止并降级交付。唯一允许跨越步骤边界的异常，且绝不到达用户。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


class Malformed(Exception):
    """某个角色纠正一次后输出仍不合契约。由流程决定如何处理（例如评审视为反对）。"""

    def __init__(self, role: str, detail: str):
        super().__init__(f"{role}: {detail}")
        self.role, self.detail = role, detail


@dataclass(frozen=True, slots=True)
class Role:
    name: str                          # 形如 "trader" 或 "judge.evidence"；点号前是预算分组
    tier: str                          # "quick" | "deep"
    instructions: str                  # 系统提示词：职责、约束。不含数据，不含输出格式。
    reads: tuple[str, ...]             # 可见的黑板键（支持 * 通配）。除此之外一概看不到。
    writes: str                        # 输出写入的黑板键
    output: type[BaseModel]            # 输出契约
    effort: Literal["low", "medium", "high"] | None = None    # 推理力度；None 表示不指定
    max_output_tokens: int = 2000

    @property
    def group(self) -> str:
        return self.name.split(".")[0]


class Models(Protocol):
    """模型端口：给一个角色，返回它这次该用的模型。真实模型、剧本、回放都实现它。"""

    def for_role(self, role: Role) -> Model: ...


@dataclass(frozen=True, slots=True)
class Limits:
    max_requests: int = 40             # 一次运行最多几次模型请求（纠正重试也算）
    max_seconds: float = 600.0         # 一次运行的总时长上限
    request_timeout_s: float = 120.0   # 单次模型请求的超时


@dataclass
class Run:
    id: str
    journal: Journal
    board: Board
    models: Models
    budget: Budget
    limits: Limits = field(default_factory=Limits)
    output_mode: OutputMode = "auto"   # auto：由模型的能力档案决定用哪种结构化输出方式
    clock: Callable[[], float] = time.monotonic
    _steps: dict[str, int] = field(default_factory=dict)
    _requests: int = 0
    _started: float | None = None
    _budget_reported: bool = False

    def step_id(self, name: str) -> str:
        n = self._steps[name] = self._steps.get(name, 0) + 1
        return f"{name}#{n}"

    def admit(self, role: Role, step: str) -> None:
        """每次运行角色前的闸门。任何一项资源用尽就停止，不再发起调用。"""
        if self._started is None:
            self._started = self.clock()
        if self._requests >= self.limits.max_requests:
            raise Halt("calls_exhausted", f"已请求 {self._requests} 次")
        if self.clock() - self._started > self.limits.max_seconds:
            raise Halt("time_exhausted", f"超过 {self.limits.max_seconds:.0f} 秒")
        if scope := self.budget.exceeded(role.group):
            if not self._budget_reported:
                self._budget_reported = True
                self.journal.append("budget.exceeded", step=step, actor=role.name,
                                    data={"scope": scope, "tokens": self.budget.tokens,
                                          "cost": self.budget.cost})
            raise Halt("budget_exhausted", scope)

    def spent(self, requests: int) -> None:
        self._requests += requests


def render(view: Mapping[str, Any]) -> str:
    """把一个角色的视图渲染成提示词正文。渲染必须是确定性的：同样的黑板，同样的文字。"""
    return "\n\n".join(f'<artifact key="{key}">\n{_text(value)}\n</artifact>'
                       for key, value in view.items())


def _text(value: Any) -> str:
    """列表每项一行，其余整体一行。紧凑、稳定、便于模型逐条引用。"""
    if isinstance(value, list):
        return "\n".join(json.dumps(v, ensure_ascii=False) for v in value) or "（空）"
    return json.dumps(value, ensure_ascii=False)


@cache
def _agent(role: Role, mode: OutputMode) -> Agent[None, Any]:
    wrap = {"native": NativeOutput, "tool": ToolOutput, "prompted": PromptedOutput}.get(mode)
    return Agent(output_type=wrap(role.output) if wrap else role.output,
                 instructions=role.instructions, name=role.name, retries=1)


def _settings(role: Role, limits: Limits) -> ModelSettings:
    settings = ModelSettings(max_tokens=role.max_output_tokens, timeout=limits.request_timeout_s)
    if role.effort is not None:
        settings["thinking"] = role.effort
    return settings


def _cost(responses: list[ModelResponse]) -> float | None:
    """这次调用的成本。价格表里没有这个模型时返回 None（未定价），不猜。"""
    try:
        return float(sum(r.cost().total_price for r in responses))
    except LookupError:
        return None


async def run_role(run: Run, role: Role) -> Any:
    step = run.step_id(role.name)
    view = run.board.view(role.reads)
    causes = [run.board.seq(key) for key in view]
    prompt = render(view)
    run.admit(role, step)

    model = run.models.for_role(role)
    usage = RunUsage()
    output: BaseModel | None = None
    failure: UnexpectedModelBehavior | None = None
    with capture_run_messages() as messages:
        try:
            result = await _agent(role, run.output_mode).run(
                prompt, model=model, usage=usage, model_settings=_settings(role, run.limits))
            output = result.output
        except UnexpectedModelBehavior as e:           # 纠正一次后仍不合契约
            failure = e
        except (ModelAPIError, UserError) as e:        # 模型不可用，或配置了它不支持的用法
            run.spent(usage.requests)
            run.journal.append("model.failed", step=step, actor=role.name,
                               data={"model": model.model_name, "error": str(e)})
            raise Halt("model_unavailable", str(e)) from e

    run.spent(usage.requests)
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    answered_by = next((r.model_name for r in responses if r.model_name), model.model_name)
    cost = _cost(responses)
    run.budget.record(role.name, answered_by, usage, cost)
    called = run.journal.append("model.called", step=step, actor=role.name, caused_by=causes, data={
        "model": answered_by,                          # 实际作答的模型，以回复自报的为准
        "prompt": prompt,
        "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
        "output": None if output is None else output.model_dump(mode="json"),
        "usage": {"requests": usage.requests, "input_tokens": usage.input_tokens,
                  "output_tokens": usage.output_tokens},
        "cost": cost,
    })
    if output is None:
        raise Malformed(role.name, str(failure)) from failure

    run.board.put(role.writes, output, by=role.name, step=step, caused_by=[called.seq])
    return output
