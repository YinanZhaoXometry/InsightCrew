"""本地网页应用：表单开一次运行、事件流看它发生、列表看过去的运行。

它只是壳：开运行用的是与命令行相同的 prepare、open_run 与 analyze；读运行只读日志文件。
除了"哪一次运行正在进行"，它自己不保存任何状态。

安全边界（BLUEPRINT 10.6）：只绑 127.0.0.1；Host 必须是本机；带 Origin 的 POST 必须来自本页面；
run_id 必须符合目录名的形状并且就在运行根目录下；一次只跑一个运行；
任何回复都不含密钥、端点或文件路径。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import webbrowser
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import Field

from insightcrew import __version__

from ..adapters.market_csv import CsvMarket
from ..domain.market import NoData, SourceUnavailable, UnknownTicker
from ..domain.types import Contract
from ..evals.scenarios import load_all
from ..flows.analyze import analyze as run_flow
from ..flows.deps import Deps
from ..kernel.runtime import Run
from . import replay_html
from .settings import Settings
from .wiring import RUNS, BadRequest, Job, closes, describe_models, market_for, open_run, prepare, today, write_page

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_.]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{4}$")
_HOSTS = ["127.0.0.1", "localhost"]
_LISTED = 50                   # 过去的运行最多列多少个
_TAIL = 4096                   # 找 run.finished 时从日志末尾读多少字节：它是最后一行，而且很短
_POLL_S = 0.1                  # 事件流在没有新内容时隔多久再看一次日志


class RunRequest(Contract):
    """表单提交的一次运行。每个字段都可以不给；文本有长度上限。"""

    ticker: str = Field(default="", max_length=32)
    as_of: str = Field(default="", max_length=10)
    scenario: str = Field(default="", max_length=64)
    judges: bool | None = None
    debate_rounds: int | None = Field(default=None, ge=0, le=10)


@dataclass
class Session:
    """服务器的全部状态。"""

    settings: Settings
    runs_dir: Path
    active: str | None = None                                   # 正在进行的运行；同一时间只有一个
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
                          judges=body.judges, debate_rounds=body.debate_rounds)
            run, deps = open_run(settings, job.request, scenario=job.scenario, flow=job.flow,
                                 runs_dir=session.runs_dir)
        except BadRequest as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        session.active = run.id
        task = asyncio.create_task(_execute(session, run, deps, job))
        session.tasks.add(task)                                 # 事件循环只弱引用任务：这里留一份
        task.add_done_callback(session.tasks.discard)
        print(f"· 运行开始：{run.id}")
        return {"run_id": run.id}

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

    return app


# ---- 表单需要知道的 ----

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
        "defaults": {"judges": settings.flow.judges, "debate_rounds": settings.flow.debate_rounds},
        "ceilings": {"run_tokens": settings.budget.run_tokens or None,
                     "run_cost": settings.budget.run_cost or None},
    }


# ---- 跑一次运行 ----

async def _execute(session: Session, run: Run, deps: Deps, job: Job) -> None:
    """在服务器的事件循环上跑一次运行，随后写运行页面。无论怎样结束，都清掉"进行中"。"""
    try:
        outcome = await run_flow(run, job.request, deps, job.flow)
        write_page(run, deps)
        print(f"· 运行结束：{run.id}（{outcome.status}，{outcome.reason}）")
    except Exception as e:      # 流程把每一种预料中的失败都变成 REVIEW；走到这里的是缺陷，事件流以 error 收尾
        # 系统错误的说明里常有文件路径，回复里不能有路径：这一类只报类名。
        session.errors[run.id] = type(e).__name__ if isinstance(e, OSError) else f"{type(e).__name__}: {e}"
        print(f"· 运行异常结束：{run.id}（{type(e).__name__}: {e}）")
    finally:
        session.active = None


# ---- 读运行：只读日志文件 ----

def _journal(session: Session, run_id: str) -> Path | None:
    """run_id 必须符合目录名的形状，并且就是运行根目录下的一个目录。URL 的其他部分不触及路径。"""
    if not RUN_ID.fullmatch(run_id):
        return None
    path = session.runs_dir / run_id / "journal.jsonl"
    return path if path.is_file() else None


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
    """一次运行的摘要：只读日志的第一行（run.started）和最后一行（run.finished），不解析中间。"""
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
        status, reason = finished.get("status", ""), finished.get("reason", "")
    else:
        status, reason = ("running" if session.active == run_id else "incomplete"), ""
    return {"run_id": run_id, "ticker": request.get("ticker", ""),
            "as_of": request.get("as_of") or "-".join(run_id.split("-")[3:6]),
            "status": status, "reason": reason, "mode": started.get("mode", "")}


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


def _end(error: str | None) -> str:
    return f"event: end\ndata: {json.dumps({'error': error}, ensure_ascii=False)}\n\n"


async def _stream(session: Session, path: Path, run_id: str, after: int) -> AsyncIterator[str]:
    """把日志逐条作为事件发出去。读的是文件而不是活的 Run，所以进行中与已结束的运行走同一条路。

    只读上次读到的位置之后新增的字节。末尾没有换行的那一段是正在写的一行，留到下一次。
    """
    offset, partial = 0, b""
    while True:
        active = session.active == run_id         # 在读文件之前记下：若此刻已不在进行，文件就不会再长
        chunk = await asyncio.to_thread(_read_from, path, offset)
        offset += len(chunk)
        *lines, partial = (partial + chunk).split(b"\n")
        for line in lines:
            if not line:
                continue
            event = json.loads(line)
            if event["seq"] > after:
                yield f"event: journal\nid: {event['seq']}\ndata: {line.decode('utf-8')}\n\n"
            if event["type"] == "run.finished":
                yield _end(None)
                return
        if run_id in session.errors:
            yield _end(session.errors[run_id])
            return
        if not active:
            yield _end("日志里没有 run.finished：这次运行没有正常结束")
            return
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
