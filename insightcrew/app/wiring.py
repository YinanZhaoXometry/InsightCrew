"""装配：把设置、行情适配器、声明、模型来源、预算与日志接成一次运行。

这是唯一构造适配器的地方。模型来源按 BLUEPRINT 10.2 的顺序选：回放 > 场景 > 真实模型
（不被强制离线且两个档位都可用）> 无代码的演示场景 > 无回复的剧本。
回退绝不静默：失败原因打印一次，不含密钥。这里不得引入 pydantic_ai：模型构造的失败
（库的 UserError 是 RuntimeError 的子类）按 (RuntimeError, ValueError) 接住即可。
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from insightcrew import __version__

from ..adapters.declarations import load_indicators, load_rules
from ..adapters.market_csv import CsvMarket
from ..adapters.models import Tiers, build
from ..adapters.scripted import Replay, Script
from ..adapters.store_fs import new_run_dir
from ..domain import symbols
from ..evals.scenarios import load_one
from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.board import Board
from ..kernel.budget import Budget, Ceiling
from ..kernel.events import Event, Journal
from ..kernel.runtime import Limits, Models, Run
from . import report
from .settings import Settings

ROOT = Path(__file__).resolve().parent.parent.parent      # 仓库根：insightcrew.toml、data/、runs/


def open_run(settings: Settings, request: dict[str, Any], *, scenario: Any = None,
             replay: tuple[Event, ...] | None = None, flow: FlowConfig | None = None,
             batch: Budget | None = None) -> tuple[Run, Deps]:
    """开一次运行：建运行目录、装配依赖、选模型来源、写 run.started。返回 Run 与代码阶段的 Deps。

    scenario 是已装载的场景对象（evals.scenarios.load_one 的结果），请求里缺的代码与日期由场景补上；
    flow 是调用方将要使用的 FlowConfig（默认 settings.flow），随 run.started 记入日志，回放才能复原
    当时的流程配置；batch 是批次层预算（消融实验用），作为本次运行预算的父层。
    """
    if scenario is not None:
        for key, value in scenario.request.items():
            request.setdefault(key, value)

    root = ROOT
    today = settings.today or date.today()
    cfg = flow or settings.flow

    market, indicators, rules = _declarations(root, settings.run.universe)

    models, mode, names = _choose_models(settings, request, scenario, replay)
    ticker = symbols.normalize(request["ticker"])
    as_of = request.get("as_of") or today.isoformat()
    run_dir = new_run_dir(root / "runs", ticker, as_of, datetime.now())

    tokens = settings.budget.run_tokens or None
    cost = settings.budget.run_cost or None
    groups = dict(settings.budget.roles)
    if scenario is not None:                      # 场景可以收紧预算（预算用尽的场景靠它）
        spec = scenario.config.budget
        tokens = spec.run_tokens or tokens
        if spec.roles:
            groups = dict(spec.roles)
    budget = Budget(Ceiling(tokens=tokens, cost=cost), groups=groups, parent=batch, name="run")
    journal = Journal(run_dir / "journal.jsonl",
                      now=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    run = Run(id=run_dir.name, journal=journal, board=Board(journal), models=models, budget=budget,
              limits=Limits(max_requests=settings.limits.max_requests,
                            max_seconds=settings.limits.max_seconds,
                            request_timeout_s=settings.limits.request_timeout_s),
              output_mode=settings.run.output_mode)

    journal.append("run.started", actor="app", data={
        "request": request,
        "version": __version__,
        "mode": mode,
        "models": names,
        "output_mode": settings.run.output_mode,
        "flow": cfg.model_dump(),
        "universe": settings.run.universe,
        "ceilings": {"run_tokens": settings.budget.run_tokens or None,
                     "run_cost": settings.budget.run_cost or None},
        "limits": {"max_requests": settings.limits.max_requests,
                   "max_seconds": settings.limits.max_seconds,
                   "request_timeout_s": settings.limits.request_timeout_s},
    })

    deps = Deps(market=market, indicators=indicators, rules=rules, today=today,
                universe=settings.run.universe, out_dir=run_dir,
                render=lambda snap: report.render(snap, journal.events, run_id=run.id))
    return run, deps


def _choose_models(settings: Settings, request: dict[str, Any], scenario: Any,
                   replay: tuple[Event, ...] | None) -> tuple[Models, str, dict[str, str]]:
    """按 10.2 的顺序选模型来源。回退到演示场景时，把它的请求补进 request（调用方传给 analyze 的就是它）。"""
    if replay is not None:
        return Replay(replay), "replay", _replay_names(replay)
    if scenario is not None:
        return Script(scenario.replies), "scripted", {"quick": "scripted", "deep": "scripted"}
    if not settings.force_offline:
        try:
            tiers = Tiers(_build(settings, settings.tiers.quick), _build(settings, settings.tiers.deep))
            return tiers, "model", tiers.names()
        except (RuntimeError, ValueError) as e:               # 缺密钥、缺端点：库的 UserError 或 ValueError
            print(f"未使用真实模型：{e}")
    if not request.get("ticker"):
        demo = load_one("demo")
        for key, value in demo.request.items():
            request.setdefault(key, value)
        return Script(demo.replies), "scripted", {"quick": "scripted", "deep": "scripted"}
    return Script({}), "scripted", {"quick": "scripted", "deep": "scripted"}


def _build(settings: Settings, name: str) -> Any:
    """把配置里的模型名变成模型。裸名字需要 IC_BASE_URL 与 IC_API_KEY。"""
    return build(name, base_url=settings.base_url,
                 api_key=settings.api_key.get_secret_value() if settings.api_key else None)


def describe_models(settings: Settings) -> tuple[str, dict[str, str], str]:
    """当前会话用哪类模型：("model" | "scripted", 各档位的名字, 不能用真实模型时的原因)。

    只做构造尝试，不打印、不发请求；网页表单与 check 命令用它说明现状。
    """
    if settings.force_offline:
        return "scripted", {"quick": "scripted", "deep": "scripted"}, "已设置 IC_FORCE_OFFLINE"
    try:
        tiers = Tiers(_build(settings, settings.tiers.quick), _build(settings, settings.tiers.deep))
        return "model", tiers.names(), ""
    except (RuntimeError, ValueError) as e:
        return "scripted", {"quick": "scripted", "deep": "scripted"}, str(e)


def _replay_names(events: tuple[Event, ...]) -> dict[str, str]:
    """回放时沿用原运行的模型名：从原 run.started 里取。"""
    for event in events:
        if event.type == "run.started":
            names = event.data.get("models") or {}
            return {tier: names.get(tier, "replay") for tier in ("quick", "deep")}
    return {"quick": "replay", "deep": "replay"}


def _declarations(root: Path, universe: str) -> tuple[CsvMarket, Any, Any]:
    """行情适配器与两份声明。这是适配器唯一的构造处。"""
    return (CsvMarket(root / "data" / "market", universe),
            load_indicators(root / "data" / "indicators.toml"),
            load_rules(root / "data" / "rules.toml"))


def build_deps(settings: Settings) -> Deps:
    """只装配依赖，不开运行。facts 命令与测试用：没有模型，没有运行目录。"""
    today = settings.today or date.today()
    market, indicators, rules = _declarations(ROOT, settings.run.universe)
    return Deps(market=market, indicators=indicators, rules=rules, today=today,
                universe=settings.run.universe)
