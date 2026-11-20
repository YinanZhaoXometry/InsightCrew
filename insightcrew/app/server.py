"""本地网页应用：表单开一次运行、事件流看它发生、列表看过去的运行。

它只是壳：开运行用的是与命令行相同的 prepare、open_run 与 wiring.run_flow（版本 2.3 起
带检查点与轨迹，BLUEPRINT 20.8 第 1 条），读运行只读日志与轨迹文件。
除了"哪一次运行正在进行"，它自己不保存任何状态。

运行控制（20.8）：取消把任务取消、在轨迹里记一行 run.cancelled，日志不写事件，
运行保持未结束、可续跑（决策 55）；续跑把 resume_run 作为活动任务跑（第 3 条），
它的拒绝都是中文、报 409。计划预览（第 8 条）不调模型、不建运行目录。

审批门（20.11，WP52）：approve 开着的运行在图的 interrupt 处暂停，活对象留在
session.pending，事件流不带 error 收尾；POST /api/runs/{id}/approval 给出决定
（接受或拒绝），从暂停的检查点续起跑完。等待期间不发生模型调用。

安全边界（BLUEPRINT 10.6）：只绑 127.0.0.1；Host 必须是本机；带 Origin 的 POST 必须来自本页面；
run_id 必须符合目录名的形状并且就在运行根目录下；一次只跑一个运行；
任何回复都不含密钥、端点或文件路径。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import time
import webbrowser
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import Field

from insightcrew import __version__

from ..adapters.market_csv import CsvMarket
from ..adapters.scripted import Script
from ..domain import symbols
from ..domain.ledger import build_ledger
from ..domain.market import InvalidTicker, NoData, SourceUnavailable, UnknownTicker
from ..domain.portfolio import state_for
from ..domain.types import Approval, Contract, Scope
from ..evals.scenarios import load_all
from ..flows import stages as flow_stages
from ..flows.analyze import ApprovalPending
from ..flows.deps import Deps
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Journal
from ..kernel.runtime import Run
from ..roles import LENSES
from . import replay_html
from .resume import resume_run
from .settings import Settings
from .trace import mark
from .wiring import (
    ROOT,
    RUNS,
    BadRequest,
    Job,
    _portfolio_for,
    build_deps,
    closes,
    describe_models,
    market_for,
    open_run,
    prepare,
    run_flow,
    today,
    write_page,
)

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_.]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{4}$")
_HOSTS = ["127.0.0.1", "localhost"]
_LISTED = 50                   # 过去的运行最多列多少个
_TAIL = 4096                   # 找 run.finished 时从日志末尾读多少字节：它是最后一行，而且很短
_POLL_S = 0.1                  # 事件流在没有新内容时隔多久再看一次日志
_KEEPALIVE_S = 15.0            # 事件流多久没有任何内容时发一行注释保活（20.8 第 4 条）
_CHANNELS = ("fundamentals", "disclosures", "news", "sentiment", "macro")


class RunRequest(Contract):
    """表单提交的一次运行。每个字段都可以不给；文本有长度上限。

    channels（20.8 第 7 条）：None（或不给）= 用设置里的 [flow] channels；空元组 = 一个也不开；
    非空元组 = 这次运行的显式选择（名字在 prepare 里校验，含 macro 需带 news）。
    portfolio：必须是 meta.portfolios 里的一个名字，或留空。"""

    ticker: str = Field(default="", max_length=32)
    as_of: str = Field(default="", max_length=10)
    scenario: str = Field(default="", max_length=64)
    judges: bool | None = None
    debate_rounds: int | None = Field(default=None, ge=0, le=10)
    portfolio: str = Field(default="", max_length=64)        # 组合文件的名字（BLUEPRINT 19.15、20.8 第 7 条）
    risk_rounds: int | None = Field(default=None, ge=0, le=10)
    tools: bool | None = None
    memory: bool | None = None
    channels: tuple[str, ...] | None = None
    approve: bool | None = None                             # 审批门（20.11）：True 才覆盖设置


class ApprovalRequest(Contract):
    """审批门的答复（BLUEPRINT 20.11）：只能接受或拒绝，可附一句备注（决策 59：不能改方案）。"""

    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)


@dataclass
class Session:
    """服务器的全部状态。"""

    settings: Settings
    runs_dir: Path
    active: str | None = None                                   # 正在进行的运行；同一时间只有一个
    task: asyncio.Task[None] | None = None                      # 那次运行（或续跑）的任务，取消要用
    pending: tuple[str, Run, Deps, Job] | None = None           # 在审批门等待确认的运行（20.11）：活对象留在这里
    errors: dict[str, str] = field(default_factory=dict)        # 以意外异常结束的运行 -> 说明
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    markets: dict[str, CsvMarket] = field(default_factory=dict)

    def market(self, universe: str) -> CsvMarket:
        if universe not in self.markets:
            self.markets[universe] = market_for(universe)
        return self.markets[universe]


def create_app(settings: Settings, runs_dir: Path | None = None) -> FastAPI:
    """构建应用而不启动任何服务。测试用 FastAPI 的测试客户端驱动它。"""
    # "今天"在启动时读一次并钉住：服务开着跨过午夜，也不会让同一个请求在两天里得到不同的默认分析日。
    settings = settings.model_copy(update={"today": today(settings)})
    session = Session(settings=settings, runs_dir=runs_dir or RUNS)
    meta = _meta(session)                                       # 模式、数据集与场景在服务期间不变

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.session = session
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_HOSTS)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        """拒绝别的页面发来的 POST；给每个回复加上不缓存、不嗅探类型的头。"""
        origin = request.headers.get("origin")
        if request.method == "POST" and origin is not None and origin != f"http://{request.headers.get('host')}":
            response: Response = JSONResponse({"error": "不接受来自其他来源的请求"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    async def page() -> HTMLResponse:
        return HTMLResponse(replay_html.live_page())

    @app.get("/api/meta")
    async def describe() -> dict[str, Any]:
        return {**meta, "active_run": session.active}

    @app.post("/api/runs", status_code=202)
    async def start(body: RunRequest) -> Any:
        if session.active is not None:
            return JSONResponse({"error": "已有一次运行正在进行，等它结束再开始下一次",
                                 "active_run": session.active}, status_code=409)
        try:
            job = prepare(settings, ticker=body.ticker, as_of=body.as_of, scenario=body.scenario,
                          judges=body.judges, debate_rounds=body.debate_rounds,
                          channels=body.channels, portfolio=_portfolio_path(body.portfolio),
                          risk_rounds=body.risk_rounds, tools=body.tools, memory=body.memory,
                          approve=True if body.approve else None)
            run, deps = open_run(settings, job.request, scenario=job.scenario, flow=job.flow,
                                 runs_dir=session.runs_dir, portfolio=job.portfolio_path)
        except BadRequest as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except ValueError:
            # 组合文件读不出来或不含契约（load_portfolio 的说明文字点名路径）：回复里不能有路径，只说结论。
            return JSONResponse({"error": "组合文件读不出来或不符合格式。"}, status_code=422)
        session.active = run.id
        task = asyncio.create_task(_execute(session, run, deps, job))
        session.task = task                                 # 事件循环只弱引用任务：这里留一份
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)
        print(f"· 运行开始：{run.id}")
        return {"run_id": run.id}

    @app.post("/api/plan")
    async def plan(body: RunRequest) -> Any:
        """计划预览（20.8 第 8 条）：不调模型、不建运行目录；prepare 拒绝的请求 422。"""
        try:
            job = prepare(settings, ticker=body.ticker, as_of=body.as_of, scenario=body.scenario,
                          judges=body.judges, debate_rounds=body.debate_rounds,
                          channels=body.channels, portfolio=_portfolio_path(body.portfolio),
                          risk_rounds=body.risk_rounds, tools=body.tools, memory=body.memory,
                          approve=True if body.approve else None)
            return _plan(session, job)
        except BadRequest as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        except ValueError:
            return JSONResponse({"error": "组合文件读不出来或不符合格式。"}, status_code=422)
        except (InvalidTicker, UnknownTicker, NoData, SourceUnavailable) as e:
            return JSONResponse({"error": f"无法建立账本：{e}"}, status_code=422)
        except OSError:
            # 系统错误的说明里常有文件路径，回复里不能有路径：只报类名。
            return JSONResponse({"error": "无法建立账本：数据集读不出来"}, status_code=422)

    @app.get("/api/runs")
    async def past_runs() -> dict[str, Any]:
        return {"runs": _past_runs(session)}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> Response:
        path = _journal(session, run_id)
        if path is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        return StreamingResponse(_stream(session, path, run_id, _resume_after(request)),
                                 media_type="text/event-stream")

    @app.get("/api/runs/{run_id}/bars")
    async def bars(run_id: str) -> Any:
        path = _journal(session, run_id)
        if path is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        return _bars(session, path)

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    async def cancel(run_id: str) -> Any:
        """取消进行中的运行（20.8 第 2 条）：取消任务、等它收尾、在轨迹里记 run.cancelled。
        日志不写事件；运行未结束、可续跑；在飞的那次调用不记用量。"""
        if _journal(session, run_id) is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        task = session.task if session.active == run_id else None
        if task is None or task.done():
            return JSONResponse({"error": "这个运行不在进行中，无法取消。",
                                 "active_run": session.active}, status_code=409)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        mark(session.runs_dir / run_id / "trace.jsonl", "run.cancelled")
        print(f"· 已取消运行：{run_id}（未结束，可续跑）")
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str) -> Any:
        """把一次未结束的运行从检查点续跑完（20.8 第 3 条）。结构性的拒绝在这里查；
        resume_run 自己的拒绝（已结束、签名不一致、2.2 之前）在它的同步前缀里，
        让出事件循环一小会儿就能在回答 202 之前把它们报成 409。"""
        path = _journal(session, run_id)
        if path is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        if session.active is not None:
            return JSONResponse({"error": "已有一次运行正在进行，等它结束再开始下一次",
                                 "active_run": session.active}, status_code=409)
        if not (session.runs_dir / run_id / "checkpoints.sqlite").is_file():
            return JSONResponse({"error": "这个运行没有检查点，无法续跑。"}, status_code=409)
        session.active = run_id
        task = asyncio.create_task(_execute_resume(session, path))
        session.task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())   # 取走异常，免得警告
        for _ in range(5):
            await asyncio.sleep(0)
            if task.done():
                break
        if task.done() and not task.cancelled() and isinstance(task.exception(), BadRequest):
            return JSONResponse({"error": str(task.exception())}, status_code=409)
        print(f"· 续跑开始：{run_id}")
        return {"run_id": run_id}

    @app.post("/api/runs/{run_id}/approval", status_code=202)
    async def approval(run_id: str, body: ApprovalRequest) -> Any:
        """给出审批门的决定（20.11）：接受或拒绝，不能改方案（决策 59）。

        只对正在等待确认的运行有效：那次运行在审批门以 interrupt 暂停，活对象留在
        session.pending；决定经 run_flow(decision=…) 从暂停的检查点续起，作为活动任务跑完。
        等待中的运行不占活动位；服务重启会丢等待位（日志与检查点仍在，拒绝并说明）。
        """
        if _journal(session, run_id) is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        if session.active is not None:
            return JSONResponse({"error": "已有一次运行正在进行，等它结束再开始下一次",
                                 "active_run": session.active}, status_code=409)
        pending = session.pending
        if pending is None or pending[0] != run_id:
            return JSONResponse({"error": "这个运行不在等待确认。", "active_run": session.active},
                                status_code=409)
        _, run, deps, job = pending
        decision = Approval(decision=body.decision, note=body.note)
        session.pending = None
        session.active = run_id
        task = asyncio.create_task(_execute(session, run, deps, job, decision=decision))
        session.task = task
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)
        print(f"· 已收到确认：{run_id}（{decision.decision}）")
        return {"run_id": run_id}

    @app.api_route("/api/runs/{run_id}/report", methods=["GET", "HEAD"])
    async def export_report(run_id: str, request: Request) -> Response:
        return _export(session, run_id, "report.md", "text/markdown; charset=utf-8", request)

    @app.api_route("/api/runs/{run_id}/replay", methods=["GET", "HEAD"])
    async def export_replay(run_id: str, request: Request) -> Response:
        return _export(session, run_id, "replay.html", "text/html; charset=utf-8", request)

    return app


# ---- 表单需要知道的 ----

def _portfolios() -> list[str]:
    """组合文件的名字：data/portfolios/ 里各 JSON 文件的 stem，按名字排序。"""
    try:
        return sorted(path.stem for path in (ROOT / "data" / "portfolios").iterdir()
                      if path.suffix == ".json")
    except OSError:
        return []


def _portfolio_path(name: str) -> str:
    """把表单选的组合名字变成仓库里的相对路径（20.8 第 7 条：页面不再能点名任意路径）。"""
    if not name:
        return ""
    if name not in _portfolios():
        raise BadRequest("没有这个组合文件：可选 " + "、".join(_portfolios()) + "，或留空。")
    return f"data/portfolios/{name}.json"


def _meta(session: Session) -> dict[str, Any]:
    settings = session.settings
    mode, models, _ = describe_models(settings)       # 不能用真实模型的原因只进控制台，不进回复
    market = session.market(settings.run.universe)
    instruments = []
    for ticker in market.tickers():
        instrument = market.instrument(ticker)
        try:
            last_bar = market.history(ticker, today(settings))[-1].date.isoformat()
        except (NoData, SourceUnavailable):
            last_bar = ""
        instruments.append({"ticker": ticker, "name": instrument.name, "market": instrument.market,
                            "currency": instrument.currency, "synthetic": instrument.synthetic,
                            "last_bar": last_bar})
    return {
        "version": __version__, "mode": mode, "models": models, "universe": settings.run.universe,
        "instruments": instruments,
        "scenarios": [{"name": s.name, "about": s.about, "ticker": s.request.get("ticker", ""),
                       "as_of": s.request.get("as_of", "")} for s in load_all()],
        "defaults": {"judges": settings.flow.judges, "debate_rounds": settings.flow.debate_rounds,
                     "risk_rounds": settings.flow.risk_rounds, "tools": settings.flow.tools,
                     "memory": settings.flow.memory, "channels": list(settings.flow.channels)},
        "channels": list(_CHANNELS),
        "portfolios": _portfolios(),
        "limits": {"max_requests": settings.limits.max_requests,
                   "max_seconds": settings.limits.max_seconds},
        "ceilings": {"run_tokens": settings.budget.run_tokens or None,
                     "run_cost": settings.budget.run_cost or None,
                     "roles": dict(settings.budget.roles)},
    }


# ---- 计划预览（20.8 第 8 条）：不调模型、不建运行目录 ----

def _plan(session: Session, job: Job) -> dict[str, Any]:
    """一份估计：会走的阶段与角色、典型与上限调用、上限、每条通道在这个分析日的覆盖、组合状态。
    通道覆盖用 facts 命令同一批函数算：在同一张临时黑板上跑一遍证据阶段（不调模型）。"""
    settings = session.settings
    deps = build_deps(settings)
    flow = job.flow
    ticker = symbols.normalize(str(job.request["ticker"]))
    instrument = deps.market.instrument(ticker)
    if job.request.get("as_of"):
        as_of = date.fromisoformat(str(job.request["as_of"]))
        as_of_text = as_of.isoformat()
    else:
        as_of = deps.market.history(ticker, deps.today)[-1].date
        as_of_text = as_of.isoformat()
    bars = deps.market.history(ticker, as_of)
    ledger, card = build_ledger(instrument, bars, as_of, deps.indicators, deps.market.dropped_rows(ticker))
    close = next((fact.value for fact in ledger if fact.id == "close"), 0.0)
    state = state_for(_portfolio_for(settings, job.portfolio_path, job.scenario),
                      ticker, close, instrument.currency)

    probe_journal = Journal()
    probe = Run(id="plan", journal=probe_journal, board=Board(probe_journal),
                models=Script({}), budget=Budget())
    probe.board.put("request/scope", Scope(ticker=instrument.ticker, as_of=as_of.isoformat(),
                                           universe=deps.universe, requested=instrument.ticker), by="plan")
    probe.board.put("ledger/instrument", instrument, by="plan")
    probe.board.put("ledger/facts", ledger, by="plan")
    probe.board.put("ledger/card", card, by="plan")
    flow_stages.evidence(probe, deps, flow.channels)

    coverage: dict[str, dict[str, str]] = {}
    for name in _CHANNELS:
        if name not in flow.channels:
            coverage[name] = {"state": "off", "reason": ""}
        else:
            status = probe.board.get(f"{name}/status")
            coverage[name] = {"state": str(status["state"]), "reason": str(status["reason"])}

    due = ["technical"] + [name for name in ("fundamentals", "disclosures", "news", "sentiment")
                           if coverage[name]["state"] == "available"]
    stages: list[dict[str, Any]] = [{"key": "ledger", "roles": [], "parallel": False},
                                    {"key": "evidence", "roles": [], "parallel": False}]
    stages += [{"key": name, "roles": [name], "parallel": flow.parallel} for name in due]
    if flow.debate_rounds:
        stages.append({"key": "debate", "roles": ["bull", "bear"], "parallel": False})
    stages.append({"key": "manager", "roles": ["manager"], "parallel": False})
    stages.append({"key": "trader", "roles": ["trader"], "parallel": False})
    if flow.risk_rounds:
        stages.append({"key": "risk",
                       "roles": ["risk.aggressive", "risk.conservative", "risk.neutral"],
                       "parallel": False})
    stages.append({"key": "audit", "roles": [], "parallel": False})
    if flow.judges:
        stages.append({"key": "judges", "roles": [f"judge.{lens}" for lens in LENSES],
                       "parallel": False})
    if flow.approve:
        stages.append({"key": "approval", "roles": [], "parallel": False})   # 20.11：等人，零模型调用
    stages.append({"key": "report", "roles": [], "parallel": False})

    typical = len(due) + 2 * flow.debate_rounds + 1 + 1 + 4 * flow.risk_rounds
    if flow.judges:
        typical += len(LENSES)          # 无返工的一轮评审：每位评审一次（"2–3"的读法见 HANDOFF 第 7 节）
    mode, _, _ = describe_models(settings)
    scenario_budget = job.scenario.config.budget if job.scenario is not None else None
    tokens = (scenario_budget.run_tokens if scenario_budget is not None and scenario_budget.run_tokens
              else settings.budget.run_tokens) or None
    cost = settings.budget.run_cost or None
    return {"ticker": instrument.ticker, "as_of": as_of_text, "mode": mode, "stages": stages,
            "calls": {"typical": typical, "ceiling": settings.limits.max_requests},
            "ceilings": {"run_tokens": tokens, "run_cost": cost},
            "channels": coverage, "portfolio": {"state": state.state, "reason": state.reason}}


# ---- 跑一次运行 ----

async def _execute(session: Session, run: Run, deps: Deps, job: Job, *,
                   decision: Approval | None = None) -> None:
    """在服务器的事件循环上跑一次运行（wiring.run_flow：带检查点与轨迹，20.8 第 1 条），
    随后写运行页面。取消是中断、不是错误（第 2 条）：日志不写事件，运行未结束、可续跑。
    decision 是审批门的决定（20.11）：从暂停的检查点续起并跑完。
    无论怎样结束，都清掉"进行中"。"""
    try:
        outcome = await run_flow(run, job.request, deps, job.flow, decision=decision)
        write_page(run, deps)
        if session.pending is not None and session.pending[0] == run.id:
            session.pending = None          # 等确认的运行已给出决定并跑完
        print(f"· 运行结束：{run.id}（{outcome.status}，{outcome.reason}）")
    except ApprovalPending:
        # 审批门暂停（20.11）：不是错误，事件流不带 error 收尾；活对象留在等待位，
        # 决定从 POST /approval 来。日志没有 run.finished：运行未结束，等待期间不发生模型调用。
        session.pending = (run.id, run, deps, job)
        write_page(run, deps)               # 为已写下的部分照写页面：结论卡据此显示「待你确认」
        print(f"· 运行暂停，等待确认：{run.id}")
    except asyncio.CancelledError:
        write_page(run, deps)          # 为已写下的部分照写页面（D6 命令行一半的先例）
        print(f"· 运行被取消：{run.id}（未结束，可续跑）")
        raise
    except Exception as e:      # 流程把每一种预料中的失败都变成 REVIEW；走到这里的是缺陷，事件流以 error 收尾
        # 系统错误的说明里常有文件路径，回复里不能有路径：这一类只报类名。
        session.errors[run.id] = type(e).__name__ if isinstance(e, OSError) else f"{type(e).__name__}: {e}"
        print(f"· 运行异常结束：{run.id}（{type(e).__name__}: {e}）")
    finally:
        session.active = None
        session.task = None


async def _execute_resume(session: Session, path: Path) -> None:
    """把一次未结束的运行从检查点续跑完（20.8 第 3 条）。页面由 resume_run 自己写。
    拒绝（BadRequest）原样抛出：等在门口的处理器把它报成 409，不记成错误。"""
    run_id = path.parent.name
    try:
        outcome = await resume_run(session.settings, path)
        print(f"· 续跑结束：{run_id}（{outcome.status}，{outcome.reason}）")
    except BadRequest as e:
        print(f"· 续跑被拒绝：{run_id}（{e}）")
        raise
    except ApprovalPending:
        # 在审批门暂停的运行被普通续跑叫醒（20.11）：没有带来决定，仍在门口等着，不算错误。
        print(f"· 续跑仍在等待确认：{run_id}")
    except asyncio.CancelledError:
        print(f"· 续跑被取消：{run_id}（未结束，仍可续跑）")
        raise
    except Exception as e:
        session.errors[run_id] = type(e).__name__ if isinstance(e, OSError) else f"{type(e).__name__}: {e}"
        print(f"· 续跑异常结束：{run_id}（{type(e).__name__}: {e}）")
    finally:
        session.active = None
        session.task = None


# ---- 读运行：只读日志与轨迹文件 ----

def _journal(session: Session, run_id: str) -> Path | None:
    """run_id 必须符合目录名的形状，并且就是运行根目录下的一个目录。URL 的其他部分不触及路径。"""
    if not RUN_ID.fullmatch(run_id):
        return None
    path = session.runs_dir / run_id / "journal.jsonl"
    return path if path.is_file() else None


def _export(session: Session, run_id: str, name: str, media_type: str, request: Request) -> Response:
    """报告或静态页作为附件导出（20.8 第 5 条）：文件不存在 404。
    也答 HEAD（不带正文）：页面用它探测导出链接在不在，才把链接画出来（20.10）。"""
    disposition = {"Content-Disposition": f'attachment; filename="{run_id}-{name}"'}
    if request.method == "HEAD":
        if RUN_ID.fullmatch(run_id) and (session.runs_dir / run_id / name).is_file():
            return Response(status_code=200, media_type=media_type, headers=disposition)
        return Response(status_code=404, headers=disposition)
    if not RUN_ID.fullmatch(run_id):
        return JSONResponse({"error": "没有这个运行"}, status_code=404)
    path = session.runs_dir / run_id / name
    if not path.is_file():
        return JSONResponse({"error": "这个运行还没有这个文件"}, status_code=404)
    return Response(path.read_bytes(), media_type=media_type, headers=disposition)


def _past_runs(session: Session) -> list[dict[str, Any]]:
    """最新的若干次运行，每次一行摘要。目录名以时刻开头，所以按名字倒序就是从新到旧。"""
    if not session.runs_dir.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for name in sorted((p.name for p in session.runs_dir.iterdir() if RUN_ID.fullmatch(p.name)), reverse=True):
        summary = _summary(session, name)
        if summary is not None:
            runs.append(summary)
        if len(runs) == _LISTED:
            break
    return runs


def _summary(session: Session, run_id: str) -> dict[str, Any] | None:
    """一次运行的摘要：只读日志的第一行（run.started）和最后一行（run.finished），不解析中间。
    resumable（20.8 第 9 条）：未结束、有签名、有检查点、不在进行中。"""
    try:
        with (session.runs_dir / run_id / "journal.jsonl").open("rb") as f:
            first = json.loads(f.readline())
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - _TAIL))
            tail = f.read()
    except (OSError, ValueError):
        return None
    if not isinstance(first, dict) or first.get("type") != "run.started":
        return None
    try:
        last = json.loads(tail.rstrip(b"\n").rpartition(b"\n")[2])
    except ValueError:
        last = {}
    finished = last["data"] if last.get("type") == "run.finished" else None
    started = first["data"]
    request = started.get("request", {})
    if finished is not None:
        status, reason, resumable = finished.get("status", ""), finished.get("reason", ""), False
    elif session.active == run_id:
        status, reason, resumable = "running", "", False
    else:
        status, reason = "incomplete", ""
        resumable = bool(started.get("thread")) and \
            (session.runs_dir / run_id / "checkpoints.sqlite").is_file()
    return {"run_id": run_id, "ticker": request.get("ticker", ""),
            "as_of": request.get("as_of") or "-".join(run_id.split("-")[3:6]),
            "status": status, "reason": reason, "mode": started.get("mode", ""),
            "resumable": resumable}


def _bars(session: Session, path: Path) -> dict[str, Any]:
    """这次运行的价格序列：准入定下的代码与分析日（request/scope），在它当时用的数据集里取。"""
    empty: dict[str, Any] = {"currency": "", "bars": []}
    universe, scope = "", None
    try:
        with path.open(encoding="utf-8") as f:
            for _, line in zip(range(4), f, strict=False):        # run.started 之后紧跟着就是 request/scope
                event = json.loads(line)
                if event["type"] == "run.started":
                    universe = event["data"].get("universe", "")
                elif event["type"] == "artifact.written" and event["data"]["key"] == "request/scope":
                    scope = event["data"]["value"]
    except (OSError, ValueError, KeyError):
        return empty
    if scope is None:                                             # 准入没有通过：没有可画的范围
        return empty
    try:
        market = session.market(universe)
        currency = market.instrument(scope["ticker"]).currency
        series = closes(market, scope["ticker"], date.fromisoformat(scope["as_of"]))
    except (OSError, ValueError, KeyError, UnknownTicker):
        return empty
    return {"currency": currency, "bars": series}


def _resume_after(request: Request) -> int:
    """事件流从哪条事件之后开始：浏览器重连时自带的 Last-Event-ID，或 ?after=<seq>，否则从头。"""
    for raw in (request.headers.get("last-event-id"), request.query_params.get("after")):
        if raw and raw.isdigit():
            return int(raw)
    return 0


def _read_from(path: Path, offset: int) -> bytes:
    with path.open("rb") as f:
        f.seek(offset)
        return f.read()


def _end(error: str | None, status: str) -> str:
    """收尾消息：error 为 null 表示正常走完；status 说明结束的是一次完成的运行还是一次中断（20.8 第 4 条）。"""
    return f"event: end\ndata: {json.dumps({'error': error, 'status': status}, ensure_ascii=False)}\n\n"


async def _stream(session: Session, path: Path, run_id: str, after: int) -> AsyncIterator[str]:
    """把日志逐条作为事件发出去，轨迹行作为 event: trace 一并送上（20.8 第 4 条）。
    读的是文件而不是活的 Run，所以进行中与已结束的运行走同一条路。

    只读上次读到的位置之后新增的字节。末尾没有换行的那一段是正在写的一行，留到下一次。
    轨迹没有 id：每次连接整份重发（页面连接时重置自己的副本），重连靠日志的序号续传。
    没有新内容的第 15 秒发一行注释保活。"""
    trace_path = path.parent / "trace.jsonl"
    journal_offset, trace_offset = 0, 0
    journal_partial = trace_partial = b""
    last_sent = time.monotonic()

    async def new_trace_lines() -> list[str]:
        nonlocal trace_offset, trace_partial
        if not trace_path.is_file():
            return []
        chunk = await asyncio.to_thread(_read_from, trace_path, trace_offset)
        trace_offset += len(chunk)
        *complete, trace_partial = (trace_partial + chunk).split(b"\n")
        return [line.decode("utf-8") for line in complete if line]

    while True:
        active = session.active == run_id         # 在读文件之前记下：若此刻已不在进行，文件就不会再长
        for line in await new_trace_lines():
            yield f"event: trace\ndata: {line}\n\n"
            last_sent = time.monotonic()
        chunk = await asyncio.to_thread(_read_from, path, journal_offset)
        journal_offset += len(chunk)
        *lines, journal_partial = (journal_partial + chunk).split(b"\n")
        for line in lines:
            if not line:
                continue
            event = json.loads(line)
            if event["seq"] > after:
                yield f"event: journal\nid: {event['seq']}\ndata: {line.decode('utf-8')}\n\n"
                last_sent = time.monotonic()
            if event["type"] == "run.finished":
                for trace_line in await new_trace_lines():        # 收尾前把轨迹读尽，end 一定在最后
                    yield f"event: trace\ndata: {trace_line}\n\n"
                yield _end(None, "finished")
                return
        if run_id in session.errors:
            yield _end(session.errors[run_id], "interrupted")
            return
        if not active:
            if session.pending is not None and session.pending[0] == run_id:
                yield _end(None, "interrupted")     # 审批门暂停（20.11）：不是错误，页面据此保持「待你确认」
                return
            yield _end("日志里没有 run.finished：这次运行没有正常结束", "interrupted")
            return
        if time.monotonic() - last_sent >= _KEEPALIVE_S:
            yield ": keep-alive\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(_POLL_S)


# ---- 启动 ----

def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    """启动本地服务并打开页面，直到 Ctrl-C。端口被占用时顺延到下一个空闲端口。"""
    application = create_app(settings)
    port = _free_port(port)
    address = f"http://127.0.0.1:{port}/"
    mode, models, reason = describe_models(settings)
    if mode == "model":
        print(f"真实模型：quick = {models['quick']}，deep = {models['deep']}。每次运行都会产生费用。")
    else:
        print(f"未使用真实模型：{reason}。只能运行离线场景。")
    print(f"InsightCrew 本地服务：{address}（Ctrl-C 停止）", flush=True)     # 输出接到管道时也要立刻看得到地址
    if open_browser:
        webbrowser.open(address)
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False, log_level="warning",
                timeout_graceful_shutdown=2)


def _free_port(port: int) -> int:
    for candidate in range(port, min(port + 50, 65536)):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise OSError(f"从 {port} 起的 50 个端口都被占用")
