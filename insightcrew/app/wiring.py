"""装配：把设置、行情适配器、声明、模型来源、预算与日志接成一次运行。

这是唯一构造适配器的地方，也是唯一读"今天"的地方。分两步：

    prepare   把用户敲进来的东西（代码、日期、场景名、流程开关）变成一份确定的任务。
              命令行与网页共用它，所以两个入口对同样的输入给同样的结果。
              任务不成立（日期无法解析、场景不存在、没有代码）在这里就以 BadRequest 拒绝。
    open_run  为一份请求建运行目录、选模型来源、建预算与日志，写下 run.started。
              不修改传入的 request。

模型来源按 BLUEPRINT 10.2 的顺序选：回放 > 场景 > 真实模型（未被强制离线且两个档位都能构造）
> 没有回复的剧本。"没有代码时改跑演示场景"这一行在 prepare 里：它决定的是跑什么，不是用什么跑。
本模块不打印；回退到离线的原因由 describe_models 交给调用方去说。
本层不得引入 pydantic_ai：模型构造的失败（库的 UserError 是 RuntimeError 的子类）
按 (RuntimeError, ValueError) 接住即可。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # 不变量 12：app 层引入 SQLite 检查点

from insightcrew import __version__

from ..adapters.decision_log import DecisionLog
from ..adapters.declarations import load_fundamentals, load_indicators, load_rules
from ..adapters.declarations_macro import load_macro
from ..adapters.declarations_tools import load_tool_indicators
from ..adapters.disclosures_json import JsonDisclosures
from ..adapters.macro_json import JsonMacro
from ..adapters.market_csv import CsvMarket
from ..adapters.models import Tiers, build
from ..adapters.news_json import JsonNews
from ..adapters.portfolio_json import load_portfolio
from ..adapters.scripted import Replay, Script
from ..adapters.social_json import JsonSocial
from ..adapters.statements_json import JsonStatements
from ..adapters.store_fs import new_run_dir
from ..domain import symbols
from ..domain.market import InvalidTicker, MarketData, NoData, SourceUnavailable, UnknownTicker
from ..domain.portfolio import Portfolio
from ..domain.portfolio import fingerprint as portfolio_fingerprint
from ..domain.types import Approval, Outcome, Scope
from ..evals.scenarios import Scenario, load_one
from ..flows.analyze import FlowConfig, preset_approval
from ..flows.analyze import drive as drive_flow
from ..flows.deps import Deps
from ..kernel.board import Board
from ..kernel.budget import Budget, Ceiling
from ..kernel.events import Event, Journal
from ..kernel.runtime import Limits, Models, Run
from . import replay_html, report
from .settings import Settings
from .trace import Tracer
from .trace import load as load_trace

ROOT = Path(__file__).resolve().parent.parent.parent      # 仓库根：insightcrew.toml、data/、runs/
RUNS = ROOT / "runs"

_SCRIPTED = {"quick": "scripted", "deep": "scripted"}
_INVALID = "INVALID"          # 代码不合规时运行目录名里的占位：用户原文不进路径
_PAGE_BARS = 250              # 运行页面的价格图最多画多少根


class BadRequest(ValueError):
    """请求在开运行之前就不成立。由命令行与网页在边界上报给用户，不进入流程。"""


@dataclass(frozen=True, slots=True)
class Job:
    """一份确定的任务：请求、它用的场景（没有则为 None）、流程配置、组合文件路径。"""

    request: dict[str, str]
    scenario: Scenario | None
    flow: FlowConfig
    portfolio_path: str = ""          # --portfolio PATH；空 = 用 [portfolio] path 或没有组合


# ---- 时钟与适配器 ----

def today(settings: Settings) -> date:
    """今天。全系统只有这里读系统日期；IC_TODAY 可以钉住它。"""
    return settings.today or date.today()


def market_for(universe: str) -> CsvMarket:
    """一个数据集的行情适配器。数据集不存在时抛 OSError。"""
    return CsvMarket(ROOT / "data" / "market", universe)


def build_deps(settings: Settings) -> Deps:
    """只装配代码阶段的依赖，不开运行。facts 命令用：没有模型，没有运行目录。

    两个证据端口按宇宙接线（BLUEPRINT 18.7 的目录布局）；宇宙里没有某标的的证据文件时，
    端口以 NotCovered 回答，通道按不可用处理，运行继续。
    """
    universe = settings.run.universe
    return Deps(market=market_for(universe),
                indicators=load_indicators(ROOT / "data" / "indicators.toml"),
                rules=load_rules(ROOT / "data" / "rules.toml"),
                today=today(settings), universe=universe,
                statements=JsonStatements(ROOT / "data" / "market", universe),
                disclosures=JsonDisclosures(ROOT / "data" / "market", universe),
                fundamentals=load_fundamentals(ROOT / "data" / "fundamentals.toml"),
                disclosure_window_days=settings.disclosures.window_days,
                disclosure_max_items=settings.disclosures.max_items,
                news=JsonNews(ROOT / "data" / "market", universe),
                social=JsonSocial(ROOT / "data" / "market", universe),
                macro=JsonMacro(ROOT / "data" / "market", universe),
                decision_log=DecisionLog(ROOT / settings.memory.path,
                                         max_entries=settings.memory.max_entries),
                tool_specs=load_tool_indicators(ROOT / "data" / "tool_indicators.toml"),
                tools_max_calls=settings.tools.max_tool_calls,
                macro_specs=load_macro(ROOT / "data" / "macro.toml"),
                news_window_days=settings.news.window_days,
                news_max_items=settings.news.max_items,
                sentiment_window_days=settings.sentiment.window_days,
                sentiment_max_posts=settings.sentiment.max_posts,
                macro_max_rows=settings.macro.max_rows,
                memory_same_ticker=settings.memory.same_ticker,
                memory_other_ticker=settings.memory.other_ticker)


# ---- 模型来源 ----

def _tiers(settings: Settings) -> tuple[Tiers | None, str]:
    """真实模型的两个档位；构造不出来时返回 None 与原因（原因里没有密钥）。不发任何请求。"""
    if settings.force_offline:
        return None, "已设置 IC_FORCE_OFFLINE"
    key = settings.api_key.get_secret_value() if settings.api_key else None
    try:
        return Tiers(build(settings.tiers.quick, base_url=settings.base_url, api_key=key),
                     build(settings.tiers.deep, base_url=settings.base_url, api_key=key)), ""
    except (RuntimeError, ValueError) as e:               # 缺密钥、缺端点
        return None, " ".join(str(e).split()).rstrip("。. ")   # 库的原话，去掉句末标点，由调用方接句子


def describe_models(settings: Settings) -> tuple[str, dict[str, str], str]:
    """不带场景的运行会用哪类模型：("model" 或 "scripted", 各档位的名字, 不能用真实模型的原因)。"""
    tiers, reason = _tiers(settings)
    if tiers is None:
        return "scripted", dict(_SCRIPTED), reason
    return "model", tiers.names(), ""


def _models(settings: Settings, scenario: Scenario | None,
            replay: tuple[Event, ...] | None) -> tuple[Models, str, dict[str, str]]:
    if replay is not None:
        recorded = next((e.data.get("models") or {} for e in replay if e.type == "run.started"), {})
        return Replay(replay), "replay", {tier: recorded.get(tier, "replay") for tier in _SCRIPTED}
    if scenario is not None:
        return Script(scenario.replies), "scripted", dict(_SCRIPTED)
    tiers, _ = _tiers(settings)
    if tiers is not None:
        return tiers, "model", tiers.names()
    return Script({}), "scripted", dict(_SCRIPTED)        # 没有回复的剧本：第一次模型调用就以 model_unavailable 停下


def _portfolio_for(settings: Settings, given: str, scenario: Scenario | None) -> Portfolio | None:
    """组合从哪来：--portfolio PATH > 场景文件内联（19.15）> [portfolio] path > 没有。"""
    if given:
        return load_portfolio(_abspath(given))
    if scenario is not None and scenario.config.portfolio:
        return Portfolio.model_validate(scenario.config.portfolio)
    if settings.portfolio.path:
        return load_portfolio(_abspath(settings.portfolio.path))
    return None


def _abspath(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


class _ReplayLog:
    """重放用的决策日志替身（决策 44）：课程按日志原样奉还，写入一概丢弃。"""

    def __init__(self, lessons: list[Any], status: dict[str, Any]) -> None:
        self.journaled_lessons, self.journaled_status = lessons, status

    def records(self) -> list[Any]:
        return []          # 原始决策日志不在日志里；课程已按记忆阶段的原样给出

    def append(self, record: Any) -> bool:
        return False       # 重放不写决策日志（19.13）


class _ScenarioLog:
    """场景内联的决策记录（19.15）：只读；场景是一次检查，不写真实日志。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def records(self) -> list[Any]:
        return list(self._rows)

    def append(self, record: Any) -> bool:
        return False


def _replay_deps(deps: Deps, replay: tuple[Event, ...]) -> Deps:
    """重放的端口替换：组合与课程取自被重放日志的黑板投影。"""
    board = Board.fold(replay).snapshot()
    updates: dict[str, Any] = {"decision_log": _ReplayLog(board.get("memory/lessons", []),
                                                          board.get("memory/status",
                                                                    {"state": "none_known",
                                                                     "reason": ""}))}
    if "portfolio/input" in board:
        updates["portfolio"] = Portfolio.model_validate(board["portfolio/input"])
    return dataclasses.replace(deps, **updates)


# ---- 第一步：把用户输入变成任务 ----

def prepare(settings: Settings, *, ticker: str = "", as_of: str = "", scenario: str = "",
            judges: bool | None = None, debate_rounds: int | None = None,
            channels: tuple[str, ...] | None = None, parallel: bool | None = None,
            portfolio: str = "", risk_rounds: int | None = None,
            tools: bool | None = None, memory: bool | None = None,
            approve: bool | None = None) -> Job:
    """命令行与网页的共同入口。空字符串与 None 表示"没有给"。

    请求：场景自带的请求打底，用户给的代码与日期覆盖它。
    没有代码也没有场景时：没有可用的真实模型就跑演示场景 demo，有则拒绝（真实模型需要一个代码）。
    流程配置：有场景用场景自己的（场景是一次完整的脚本化运行，--channels 与设置里的
    channels 都被忽略，BLUEPRINT 18.8），否则用设置里的；judges、debate_rounds 与
    channels 是用户在这一次运行上的显式覆盖；未知的通道名在这里拒绝。
    approve（20.11）只在明确给出（True）时覆盖：审批门默认关着。
    """
    chosen = _load_scenario(scenario) if scenario else None
    request = dict(chosen.request) if chosen else {}
    if ticker.strip():
        request["ticker"] = ticker.strip()
    if as_of.strip():
        request["as_of"] = _iso_date(as_of.strip())
    if "ticker" not in request:
        mode, _, _ = describe_models(settings)
        if mode == "model":
            raise BadRequest("没有给出代码。真实模型的运行需要一个代码，例如 ACME 或 0700.HK；"
                             "离线场景用 --scenario 指定。")
        chosen = _load_scenario("demo")
        request = {**chosen.request, **request}

    base = chosen.config.flow if chosen else settings.flow
    # D4（BLUEPRINT 20.7 第 7 条）：场景说了算；没有场景时，--channels 没给（None）就用
    # 设置里的 [flow] channels，给了（含空元组）才是这一次运行的显式覆盖。
    names = None if chosen is not None or channels is None else tuple(_channel_names(channels))
    overrides = {key: value for key, value in
                 (("judges", judges), ("debate_rounds", debate_rounds), ("channels", names),
                  ("parallel", parallel), ("risk_rounds", risk_rounds), ("tools", tools),
                  ("approve", approve))
                 if value is not None}
    if memory is not None:
        overrides["memory"] = memory
    if settings.memory.enabled:
        overrides.setdefault("memory", True)         # [memory] enabled 是文件级的默认开
    return Job(request=request, scenario=chosen,
               flow=FlowConfig.model_validate({**base.model_dump(), **overrides}),
               portfolio_path=portfolio)


_CHANNEL_NAMES = ("fundamentals", "disclosures", "news", "sentiment", "macro")


def _channel_names(channels: tuple[str, ...]) -> tuple[str, ...]:
    """把 --channels 的名字校验成通道名；未知名在这里拒绝，不等到运行里。
    macro 需要同时点名 news（19.12：宏观证据随新闻通道一起收集）。
    从不以 None 调用（D4 修复后 prepare 先判 None），空元组表示"一个也不开"。"""
    unknown = [name for name in channels if name not in _CHANNEL_NAMES]
    if unknown:
        raise BadRequest(f"未知的证据通道：{'、'.join(unknown)}。"
                         "可用：fundamentals、disclosures、news、sentiment、macro。")
    if "macro" in channels and "news" not in channels:
        raise BadRequest("宏观通道（macro）需要与 news 一起点名：宏观证据随新闻通道一起收集。")
    deduped = [name for name in _CHANNEL_NAMES if name in channels]
    return tuple(deduped)


def _load_scenario(name: str) -> Scenario:
    try:
        return load_one(name)
    except FileNotFoundError as e:
        raise BadRequest(str(e)) from e


def _iso_date(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise BadRequest(f"日期 {text!r} 无法解析：请写成 YYYY-MM-DD，例如 2026-09-30") from None


# ---- 第二步：开一次运行 ----

def open_run(settings: Settings, request: dict[str, Any], *, scenario: Scenario | None = None,
             replay: tuple[Event, ...] | None = None, flow: FlowConfig | None = None,
             batch: Budget | None = None, runs_dir: Path | None = None,
             portfolio: str = "") -> tuple[Run, Deps]:
    """建运行目录、装配依赖、选模型来源、写 run.started。返回 Run 与代码阶段的 Deps。

    request 至少有 "ticker"，可以有 "as_of"（ISO 日期）；不成立时抛 BadRequest，此时什么都没有创建。
    scenario 给出预先写好的回复，并可以收紧预算；replay 是一份日志里的事件，模型回复从中取回。
    flow 是调用方将要交给 analyze 的流程配置，随 run.started 记入日志，回放据此复原。
    batch 是批次层预算，作为本次运行预算的父层。runs_dir 缺省为仓库的 runs/。
    """
    request = dict(request)
    if not request.get("ticker"):
        raise BadRequest("请求里没有代码")
    if request.get("as_of"):
        request["as_of"] = _iso_date(str(request["as_of"]))

    deps = build_deps(settings)
    deps = dataclasses.replace(deps, portfolio=_portfolio_for(settings, portfolio, scenario))
    if replay is not None:
        deps = _replay_deps(deps, replay)          # 决策 44：组合与课程按日志供给，重放不读外部文件
    elif scenario is not None and scenario.config.memory:
        deps = dataclasses.replace(deps, decision_log=_ScenarioLog(scenario.config.memory))
    models, mode, names = _models(settings, scenario, replay)

    # 实际生效的预算上限：场景可以收紧它（预算用尽的两个场景靠这个）。记进 run.started，回放才能复原。
    tokens = settings.budget.run_tokens or None
    cost = settings.budget.run_cost or None
    groups = dict(settings.budget.roles)
    if scenario is not None:
        tokens = scenario.config.budget.run_tokens or tokens
        groups = dict(scenario.config.budget.roles) or groups
    limits = Limits(max_requests=settings.limits.max_requests, max_seconds=settings.limits.max_seconds,
                    request_timeout_s=settings.limits.request_timeout_s)
    cfg = flow or settings.flow

    run_dir = new_run_dir(runs_dir or RUNS, *_dir_labels(request, deps), datetime.now())
    journal = Journal(run_dir / "journal.jsonl",
                      now=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    run = Run(id=run_dir.name, journal=journal, board=Board(journal), models=models,
              budget=Budget(Ceiling(tokens=tokens, cost=cost), groups=groups, parent=batch, name="run"),
              limits=limits, output_mode=settings.run.output_mode)
    journal.append("run.started", actor="app", data={
        "request": request,
        "version": __version__,
        "mode": mode,
        "models": names,
        "output_mode": settings.run.output_mode,
        "flow": cfg.model_dump(mode="json"),
        "universe": settings.run.universe,
        "ceilings": {"run_tokens": tokens, "run_cost": cost, "roles": groups},
        "limits": {"max_requests": limits.max_requests, "max_seconds": limits.max_seconds,
                   "request_timeout_s": limits.request_timeout_s},
        "thread": run_signature(request, deps, cfg, names),   # 运行签名（19.5 第 6 条）：resume 核对用
        # 组合原件：resume 据此重算签名并接着用它（问题 43），组合阶段之前中断的运行也复原得了
        "portfolio": deps.portfolio.model_dump(mode="json") if deps.portfolio is not None else None,
    })
    if cfg.approve:
        _preset_approval(run, scenario, replay)     # 20.11：场景/重放自带决定时预置，图到门口入账、不暂停
    return run, dataclasses.replace(
        deps, out_dir=run_dir,
        render=lambda snapshot: report.render(snapshot, journal.events, run_id=run.id))


def _preset_approval(run: Run, scenario: Scenario | None, replay: tuple[Event, ...] | None) -> None:
    """预置审批门的自带决定（BLUEPRINT 20.11，决策 44 对外部输入的做法）：场景写在自己的
    replies["user"] 里（第一条）；重放从被重放日志的黑板投影取回。决定经 flows 的注册表
    预置、由 approval 节点在门口入账——不提前写日志，运行的事件次序与序号和一次真实运行
    完全相同（重放才能逐序号一致）。没有预置时真实运行在门口暂停等人：命令行 --approve
    问终端，页面 POST /api/runs/<id>/approval。"""
    supplied: Any = None
    if scenario is not None:
        replies = scenario.replies.get("user")
        supplied = replies[0] if replies else None
    elif replay is not None:
        supplied = Board.fold(replay).snapshot().get("approval/decision")
    if supplied is not None:
        preset_approval(run, Approval.model_validate(supplied))


def _dir_labels(request: dict[str, Any], deps: Deps) -> tuple[str, str]:
    """运行目录名里的代码与分析日。两者都不含用户原文：代码先规范化（不合规就用占位），
    日期已是 ISO；没给日期时取准入将会采用的那一天，取不到（代码不在数据集里等）就用今天。"""
    try:
        ticker = symbols.normalize(str(request["ticker"]))
    except InvalidTicker:
        return _INVALID, request.get("as_of") or deps.today.isoformat()
    if request.get("as_of"):
        return ticker, request["as_of"]
    try:
        return ticker, deps.market.history(ticker, deps.today)[-1].date.isoformat()
    except (UnknownTicker, NoData, SourceUnavailable):
        return ticker, deps.today.isoformat()


# ---- 检查点与运行签名（BLUEPRINT 19.5 第 6 条） ----


def run_signature(request: dict[str, Any], deps: Deps, flow: FlowConfig,
                  tier_names: dict[str, str]) -> str:
    """运行签名：代码、分析日、整个流程配置、组合指纹、档位名的 SHA-256 前 16 位十六进制。

    续跑以此为线程 ID：同一签名才允许接着既有检查点继续（决策 44 的精神：续跑的输入必须与原运行一致）。
    """
    ticker, as_of = _dir_labels(dict(request), deps)
    payload = json.dumps(
        [ticker, as_of, flow.model_dump(mode="json"), portfolio_fingerprint(deps.portfolio),
         sorted(tier_names.values())], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@asynccontextmanager
async def checkpoint(run_dir: Path) -> AsyncIterator[AsyncSqliteSaver]:
    """真实运行的检查点：运行目录下的 checkpoints.sqlite（异步实现，异步图用）。"""
    async with AsyncSqliteSaver.from_conn_string(str(run_dir / "checkpoints.sqlite")) as saver:
        yield saver


async def run_flow(run: Run, request: dict[str, Any], deps: Deps,
                   flow: FlowConfig | None = None, *,
                   decision: Approval | None = None,
                   ask: Callable[[Run], Approval] | None = None) -> Outcome:
    """命令行与网页的真实运行入口：带 SQLite 检查点、运行签名与 trace，供 resume 续跑。

    场景、测试与 replay 直接调 flows.analyze.analyze（无检查点、无 trace）。
    trace（BLUEPRINT 20.6）在这里挂成 run.observe：调用方已挂的观察者（命令行 --show
    的逐行播报）不被顶掉，先记测量、再转交。测量不影响日志（不变量 22）。
    decision/ask（20.11）是审批门的两个入口：decision 给出已拿到的决定（从暂停的
    检查点续起，页面的确认按钮走它）；ask 给出暂停后的问询（命令行 --approve 走它）。
    """
    cfg = flow or FlowConfig()
    started = next((e for e in run.journal.events if e.type == "run.started"), None)
    names = (started.data.get("models") if started is not None else None) or {}
    thread = run_signature(request, deps, cfg, dict(names))
    if deps.out_dir is None:
        return await drive_flow(run, request, deps, cfg, decision=decision, ask=ask)
    tracer = Tracer(deps.out_dir / "trace.jsonl")
    prior = run.observe

    def _observing(kind: str, data: Mapping[str, Any]) -> None:
        tracer(kind, data)
        if prior is not None:
            prior(kind, data)

    run.observe = _observing
    async with checkpoint(deps.out_dir) as saver:
        return await drive_flow(run, request, deps, cfg, checkpointer=saver, thread_id=thread,
                                decision=decision, ask=ask)


# ---- 运行结束之后：运行页面 ----

def closes(market: MarketData, ticker: str, as_of: date) -> list[dict[str, Any]]:
    """运行页面价格图用的收盘价：分析日（含）之前最近的若干根。读不到就是空列表。"""
    try:
        bars = market.history(ticker, as_of)
    except (UnknownTicker, NoData, SourceUnavailable):
        return []
    return [{"date": bar.date.isoformat(), "close": bar.close} for bar in bars[-_PAGE_BARS:]]


def write_page(run: Run, deps: Deps) -> Path | None:
    """把运行页面（replay.html）写进运行目录。准入通过的运行带上价格序列；
    运行留了轨迹（20.6）就把轨迹一并嵌进静态页（20.8 第 10 条）。"""
    if deps.out_dir is None:
        return None
    bars = None
    if run.board.has("request/scope"):
        scope = run.board.get("request/scope", Scope)
        bars = closes(deps.market, scope.ticker, date.fromisoformat(scope.as_of)) or None
    trace_path = deps.out_dir / "trace.jsonl"
    trace = load_trace(trace_path) if trace_path.is_file() else None
    return replay_html.write(deps.out_dir, run.journal.events, bars, trace)
