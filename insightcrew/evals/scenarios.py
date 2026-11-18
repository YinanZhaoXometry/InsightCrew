"""场景：一次脚本化运行加它的预期结果，既是演示也是测试。

场景文件在 data/scenarios/<名字>.json，装载时按契约校验。运行评测的入口在这里，
但开运行这件事由调用方交给本模块一个函数（evals 不 import app）。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ..domain.types import Contract
from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.runtime import Run

_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "scenarios"


class BudgetSpec(Contract):
    """场景对预算的覆盖：0 表示沿用设置里的值（也就是默认不设上限）。"""

    run_tokens: int = 0
    roles: dict[str, int] = Field(default_factory=dict)


class Config(Contract):
    """场景的流程与预算覆盖。"""

    flow: FlowConfig = FlowConfig()
    budget: BudgetSpec = BudgetSpec()


class Expect(Contract):
    """预期的结果与调用序列。"""

    status: Literal["delivered", "review"]
    reason: str = "ok"
    facts_and_rules: Literal["verified", "unverified"] = "verified"
    judgment: str = "accepted 2/3"
    calls: tuple[str, ...] = ()


class Scenario(Contract):
    """一个场景：请求、每个角色预先写好的回答、配置覆盖、预期。"""

    name: str
    about: str
    request: dict[str, str]
    replies: dict[str, list[dict[str, Any] | str]]
    config: Config = Config()
    expect: Expect


@dataclass(frozen=True, slots=True)
class Result:
    """一个场景的比对结果。ok 为 False 时 detail 说明每一处不符。"""

    name: str
    ok: bool
    detail: str


def load_one(name: str) -> Scenario:
    """按名字装载一个场景。文件不存在或字段不合契约都直接报错。"""
    path = _DIR / f"{name}.json"
    if not path.exists():
        available = "、".join(p.stem for p in sorted(_DIR.glob("*.json"))) or "（无）"
        raise FileNotFoundError(f"没有名为 {name!r} 的场景。可用的场景：{available}")
    return Scenario.model_validate_json(path.read_text(encoding="utf-8"))


def load_all() -> list[Scenario]:
    """装载 data/scenarios/ 下的全部场景，按文件名排序。"""
    out = []
    for path in sorted(_DIR.glob("*.json")):
        out.append(Scenario.model_validate_json(path.read_text(encoding="utf-8")))
    return out


async def run_one(scenario: Scenario, open_run: Callable[..., tuple[Run, Deps]],
                  analyze: Callable[..., Awaitable[Any]]) -> Result:
    """跑一个场景并比对其结果。open_run 由调用方绑定好设置后传入；analyze 是 flows.analyze.analyze。"""
    request = dict(scenario.request)
    run, deps = open_run(request, scenario=scenario, flow=scenario.config.flow)
    outcome = await analyze(run, request, deps, scenario.config.flow)
    calls = tuple(e.actor for e in run.journal.events if e.type == "model.called")
    expect = scenario.expect
    problems: list[str] = []
    if outcome.status != expect.status:
        problems.append(f"状态：预期 {expect.status}，实际 {outcome.status}（原因 {outcome.reason}）")
    if outcome.reason != expect.reason:
        problems.append(f"原因：预期 {expect.reason}，实际 {outcome.reason}")
    if outcome.facts_and_rules != expect.facts_and_rules:
        problems.append(f"核验：预期 {expect.facts_and_rules}，实际 {outcome.facts_and_rules}")
    if outcome.judgment != expect.judgment:
        problems.append(f"判断：预期 {expect.judgment}，实际 {outcome.judgment}")
    if calls != expect.calls:
        problems.append(f"调用：预期 {list(expect.calls)}，实际 {list(calls)}")
    detail = "；".join(problems) if problems else "相符"
    return Result(name=scenario.name, ok=not problems, detail=detail)


async def run_all(open_run: Callable[..., tuple[Run, Deps]], analyze: Callable[..., Awaitable[Any]],
                  names: Sequence[str] | None = None) -> list[Result]:
    """跑全部场景（或指定的几个），按文件名顺序。"""
    scenarios = load_all() if names is None else [load_one(n) for n in names]
    return [await run_one(s, open_run, analyze) for s in scenarios]


def names() -> list[str]:
    """全部场景的名字，按文件名排序。"""
    return [p.stem for p in sorted(_DIR.glob("*.json"))]


def summary_line(result: Result) -> str:
    """一行结果：名字 + 相符/不符 + 细节。"""
    mark = "✓" if result.ok else "✗"
    return f"{mark} {result.name}：{result.detail}"


def to_json(scenario: Scenario) -> str:
    """把场景写成 JSON（indent=2，中文不转义），生成场景文件用。"""
    return json.dumps(scenario.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
