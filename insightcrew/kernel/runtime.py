"""一次运行的上下文，以及角色执行器。

run_role 是系统里唯一运行智能体的地方。它做且只做五件事：
    取视图 -> 渲染提示词 -> 过闸门（次数、时间、预算）-> 运行智能体并记账 -> 写黑板

模型调用、结构化输出、输出不合契约时的一次纠正重试、各家模型的能力差异，
都交给 Pydantic AI 的 Agent；这里不重复实现。

版本 2.2（BLUEPRINT 19.6）把它拆成两半：run_role_deferred 算，commit 入账；
run_role 等于两半相连，产生的事件与拆分前完全一致。带工具的角色在计算这半
把 Run.tools 点名的函数交给 Agent；内核只加统一的限次与重复缓存包装，
不认识任何一个具体的工具名。
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
    FunctionToolset,
    ModelAPIError,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ModelSettings,
    NativeOutput,
    PromptedOutput,
    RunContext,
    ToolOutput,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
    capture_run_messages,
)
from pydantic_ai.models import Model
from pydantic_ai.toolsets import ToolsetTool
from pydantic_ai.usage import RunUsage, UsageLimits

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
    tools: tuple[str, ...] = ()        # 可用的工具名；名字到函数的映射由 Run.tools 提供
    max_tool_calls: int = 0            # 一次调用里最多真正执行几次工具（0 = 一个也不执行）

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
    tools: Mapping[str, Callable[..., Any]] = field(default_factory=dict)   # 工具名 -> 函数；默认没有工具
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


@dataclass
class _ToolLedger:
    """一次角色调用的工具台账：真正执行了几次、其中多少次是重复调用、每次调用的结果。"""

    cap: int
    calls: int = 0                     # 真正执行了工具的次数
    repeated: int = 0                  # 同名同参的重复调用，直接回放缓存、没有执行
    results: dict[tuple[str, str], Any] = field(default_factory=dict)

    def seen(self, name: str, args: dict[str, Any]) -> tuple[str, str]:
        """把一次调用的参数变成可比较的键：同名同参即同键。"""
        return name, json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)


class _CappedTools(FunctionToolset[_ToolLedger]):
    """内核给所有工具加的同一层包装（BLUEPRINT 19.6 第 2 条）。内核不认识任何具体的工具名：
    超过 max_tool_calls 次执行后，后续每次调用都不再执行，直接要求模型给出最终输出；
    同名同参的重复调用回放上次的结果，不重新计算。台账随 deps 逐次调用传入。"""

    async def call_tool(self, name: str, tool_args: dict[str, Any],
                        ctx: RunContext[_ToolLedger], tool: ToolsetTool[_ToolLedger]) -> Any:
        ledger = ctx.deps
        key = ledger.seen(name, tool_args)
        if ledger.calls >= ledger.cap:                 # 上限之后的每次调用都只回这一句
            return "已达到工具调用上限，请立即给出最终输出。"
        if key in ledger.results:
            ledger.repeated += 1
            return ledger.results[key]
        result = await super().call_tool(name, tool_args, ctx, tool)
        ledger.calls += 1
        ledger.results[key] = result
        return result


@cache
def _agent(role: Role, mode: OutputMode,
           functions: tuple[Callable[..., Any], ...] = ()) -> Agent[Any, Any]:
    """缓存键含工具名：工具名是 Role 的字段，点名不同的角色不共用 Agent；
    函数本身也在键里，名字相同而函数不同的两次装配也不会共用旧的。"""
    wrap = {"native": NativeOutput, "tool": ToolOutput, "prompted": PromptedOutput}.get(mode)
    return Agent(output_type=wrap(role.output) if wrap else role.output,
                 instructions=role.instructions, name=role.name, retries=1,
                 toolsets=[_CappedTools(functions)] if functions else None)


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


@dataclass(frozen=True, slots=True)
class Deferred:
    """一次已经算好、还未入账的角色执行（BLUEPRINT 19.6 第 1 条）。

    commit 之前不写日志、不上黑板；谁先算完不打紧，入账顺序由 commit 的调用顺序决定。
    事件序号只能由日志在追加时分配，所以这里存的是入账要用的全部内容，不是 Event。
    """

    role: Role
    step: str
    caused_by: tuple[int, ...]         # 视图里各键当时的最新版本序号
    data: dict[str, Any]               # model.called 的 data（model/prompt/messages/output/usage/cost…）
    output: BaseModel | None           # 已校验的输出；None 表示纠正一次后仍不合契约
    usage: RunUsage
    cost: float | None
    failure: Exception | None          # output 为 None 时的原因，commit 时转为 Malformed


async def run_role_deferred(run: Run, role: Role) -> Deferred:
    """run_role 的前半段：取视图、渲染、过闸门、运行智能体、备好入账材料。

    闸门（run.admit）在请求之前，超限时照旧写 budget.exceeded 并抛 Halt；
    模型故障照旧写 model.failed 并抛 Halt。除这两类"运行已中止"的事件外不写日志。
    """
    step = run.step_id(role.name)
    view = run.board.view(role.reads)
    causes = [run.board.seq(key) for key in view]
    prompt = render(view)
    run.admit(role, step)

    functions = tuple(run.tools[name] for name in role.tools)   # 点名的工具才可见
    ledger = _ToolLedger(role.max_tool_calls) if role.tools else None
    model = run.models.for_role(role)
    usage = RunUsage()
    output: BaseModel | None = None
    failure: Exception | None = None
    with capture_run_messages() as messages:
        try:
            result = await _agent(role, run.output_mode, functions).run(
                prompt, model=model, usage=usage, model_settings=_settings(role, run.limits),
                usage_limits=UsageLimits(request_limit=role.max_tool_calls + 2) if role.tools else None,
                deps=ledger)
            output = result.output
        except UnexpectedModelBehavior as e:           # 纠正一次后仍不合契约
            failure = e
        except UsageLimitExceeded as e:                # 工具轮次用尽（请求数到 max_tool_calls + 2）仍无最终输出
            failure = e
        except (ModelAPIError, UserError) as e:        # 模型不可用，或配置了它不支持的用法
            run.spent(usage.requests)
            run.journal.append("model.failed", step=step, actor=role.name,
                               data={"model": model.model_name, "error": str(e)})
            raise Halt("model_unavailable", str(e)) from e

    responses = [m for m in messages if isinstance(m, ModelResponse)]
    answered_by = next((r.model_name for r in responses if r.model_name), model.model_name)
    cost = _cost(responses)
    data: dict[str, Any] = {
        "model": answered_by,                          # 实际作答的模型，以回复自报的为准
        "prompt": prompt,
        "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
        "output": None if output is None else output.model_dump(mode="json"),
        "usage": {"requests": usage.requests, "input_tokens": usage.input_tokens,
                  "output_tokens": usage.output_tokens},
        "cost": cost,
    }
    if ledger is not None:                             # 工具角色的两项计数（工具调用与结果本身在 messages 里）
        data["tool_calls"] = ledger.calls
        data["repeated_tool_calls"] = ledger.repeated
    return Deferred(role=role, step=step, caused_by=tuple(causes), data=data,
                    output=output, usage=usage, cost=cost, failure=failure)


def commit(run: Run, deferred: Deferred) -> Any:
    """run_role 的后半段：记账、写日志、上黑板。谁先算完不打紧，这里才定先后。"""
    run.spent(deferred.usage.requests)
    run.budget.record(deferred.role.name, deferred.data["model"], deferred.usage, deferred.cost)
    called = run.journal.append("model.called", step=deferred.step, actor=deferred.role.name,
                                caused_by=deferred.caused_by, data=deferred.data)
    if deferred.output is None:
        raise Malformed(deferred.role.name, str(deferred.failure)) from deferred.failure

    run.board.put(deferred.role.writes, deferred.output, by=deferred.role.name,
                  step=deferred.step, caused_by=[called.seq])
    return deferred.output


async def run_role(run: Run, role: Role) -> Any:
    """先算后记（版本 2.2 起拆成 run_role_deferred + commit），事件与拆分前完全一致。"""
    return commit(run, await run_role_deferred(run, role))
