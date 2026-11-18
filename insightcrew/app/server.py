"""本地网页应用：表单开一次运行、事件流看它发生、列表看过去的运行。

它只是壳：开运行用的是与命令行相同的 open_run 与 analyze；读运行只读日志文件。
安全边界（BLUEPRINT 10.6）：只绑 127.0.0.1；Host 与 Origin 校验；run_id 必须匹配目录名的形状；
一次只跑一个运行；任何回复都不含密钥、端点或路径。
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import webbrowser
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field

from ..adapters.market_csv import CsvMarket
from ..domain import symbols
from ..domain.types import Contract
from ..evals.scenarios import Scenario, load_all
from ..flows.analyze import FlowConfig
from ..flows.analyze import analyze as run_flow
from ..flows.deps import Deps
from ..kernel.events import Event, Journal
from ..kernel.runtime import Run
from . import replay_html
from .settings import Settings
from .wiring import ROOT, describe_models, open_run

RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_.]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{4}$")


class RunRequest(Contract):
    """表单提交的一次运行。每个字段可选；文本有长度上限。"""

    ticker: str = Field(default="", max_length=32)
    as_of: str = Field(default="", max_length=10)
    scenario: str = Field(default="", max_length=64)
    judges: bool | None = None
    debate_rounds: int | None = Field(default=None, ge=0, le=10)


def create_app(settings: Settings) -> FastAPI:
    """构建应用而不启动任何服务。测试用 FastAPI 的测试客户端驱动它。"""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.active = None                      # 正在运行的 run_id；同一时间只有一个
    app.state.error = {}                         # 意外异常的 run_id -> 说明（日志里不会有 run.finished）

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def _headers_and_origin(request: Request, call_next):
        origin = request.headers.get("Origin")
        if origin:
            host = origin.split("://", 1)[-1].split(":", 1)[0] if "://" in origin else origin
            if host not in ("127.0.0.1", "localhost"):
                return JSONResponse({"error": "不接受跨站请求"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    async def page() -> Response:
        return Response(replay_html.live_page(), media_type="text/html")

    @app.get("/api/meta")
    async def meta():
        settings = app.state.settings
        mode, models, _reason = describe_models(settings)
        market = CsvMarket(ROOT / "data" / "market", settings.run.universe)
        today = settings.today or date.today()
        instruments = []
        for ticker in _universe_tickers(settings.run.universe):
            instrument = market.instrument(ticker)
            bars = market.history(ticker, today)
            instruments.append({"ticker": instrument.ticker, "name": instrument.name,
                                "market": instrument.market, "currency": instrument.currency,
                                "synthetic": instrument.synthetic,
                                "last_bar": bars[-1].date.isoformat() if bars else ""})
        scenarios = [{"name": s.name, "about": s.about, "ticker": s.request.get("ticker", ""),
                      "as_of": s.request.get("as_of", "")} for s in load_all()]
        return {"version": _version(), "mode": mode, "models": models,
                "universe": settings.run.universe, "instruments": instruments,
                "scenarios": scenarios,
                "defaults": {"judges": settings.flow.judges,
                             "debate_rounds": settings.flow.debate_rounds},
                "ceilings": {"run_tokens": settings.budget.run_tokens or None,
                             "run_cost": settings.budget.run_cost or None},
                "active_run": app.state.active}

    @app.post("/api/runs", status_code=202)
    async def start(body: RunRequest):
        if app.state.active is not None:
            return JSONResponse({"error": "已有运行在进行", "active_run": app.state.active},
                                status_code=409)
        request: dict[str, str] = {}
        if body.ticker:
            try:
                request["ticker"] = symbols.normalize(body.ticker)
            except Exception:
                return JSONResponse({"error": "代码格式不正确"}, status_code=422)
        if body.as_of:
            request["as_of"] = body.as_of
        scenario = _scenario(body.scenario)
        if body.scenario and scenario is None:
            return JSONResponse({"error": f"没有名为 {body.scenario!r} 的场景"}, status_code=422)
        flow = _flow(app.state.settings, body)

        run, deps = open_run(app.state.settings, request, scenario=scenario, flow=flow)
        app.state.active = run.id
        loop = asyncio.get_running_loop()
        task = loop.create_task(_run_task(app, run, deps, request, flow))
        app.state.task = task
        print(f"· 运行开始：{run.id}（{request.get('ticker', '演示场景')}）")
        return {"run_id": run.id}

    @app.get("/api/runs")
    async def past_runs():
        runs = []
        for path in sorted(_runs_root().iterdir(), reverse=True):
            journal = path / "journal.jsonl"
            if not (path.is_dir() and journal.exists()):
                continue
            info = _run_info(path)
            if info:
                runs.append(info)
            if len(runs) >= 50:
                break
        return {"runs": runs}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request) -> Response:
        path = _journal(run_id)
        if path is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        after = _after_seq(request)
        return StreamingResponse(_stream(app, path, run_id, after),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/runs/{run_id}/bars")
    async def bars(run_id: str):
        path = _journal(run_id)
        if path is None:
            return JSONResponse({"error": "没有这个运行"}, status_code=404)
        scope, universe = _scope_of(path)
        if scope is None:
            return {"currency": "", "bars": []}
        market = CsvMarket(ROOT / "data" / "market", universe)
        try:
            instrument = market.instrument(scope["ticker"])
            series = market.history(scope["ticker"], date.fromisoformat(scope["as_of"]))
        except Exception:
            return {"currency": "", "bars": []}
        tail = [{"date": bar.date.isoformat(), "close": bar.close} for bar in series[-250:]]
        return {"currency": instrument.currency, "bars": tail}

    return app


async def _run_task(app: FastAPI, run: Run, deps: Deps, request: dict, flow: FlowConfig) -> None:
    """在服务器的事件循环上跑一次运行，随后写回放页、清出“进行中”标记。"""
    try:
        await run_flow(run, request, deps, flow)
        if deps.out_dir is not None:
            replay_html.write(deps.out_dir, run.journal.events, _bars_of(run, deps))
        print(f"· 运行结束：{run.id}（{run.board.get('run/outcome')['status']}）")
    except Exception as e:                                     # 意外异常是缺陷：流里会以 error 收尾
        app.state.error[run.id] = f"{type(e).__name__}: {e}"
        print(f"· 运行异常结束：{run.id}（{type(e).__name__}）")
    finally:
        app.state.active = None


def _bars_of(run: Run, deps: Deps) -> list[dict] | None:
    """回放页里嵌的价格序列：截至分析日的收盘价。拿不到就不嵌。"""
    try:
        board = run.board
        if not board.has("request/scope"):
            return None
        scope = board.get("request/scope")
        market = deps.market
        series = market.history(scope["ticker"], date.fromisoformat(scope["as_of"]))
        return [{"date": b.date.isoformat(), "close": b.close} for b in series]
    except Exception:
        return None


def _scenario(name: str) -> Scenario | None:
    if not name:
        return None
    matches = [s for s in load_all() if s.name == name]
    return matches[0] if matches else None


def _flow(settings: Settings, body: RunRequest) -> FlowConfig:
    over: dict = {}
    if body.judges is not None:
        over["judges"] = body.judges
    if body.debate_rounds is not None:
        over["debate_rounds"] = body.debate_rounds
    return FlowConfig(**{**settings.flow.model_dump(), **over})


def _runs_root() -> Path:
    return ROOT / "runs"


def _universe_tickers(universe: str) -> list[str]:
    """数据集里的代码清单：universe.toml 的表名，按出现顺序。"""
    import tomllib
    path = ROOT / "data" / "market" / universe / "universe.toml"
    with path.open("rb") as handle:
        return list(tomllib.load(handle))


def _journal(run_id: str) -> Path | None:
    """run_id 必须匹配目录名的形状，并直接位于运行根目录下；其余一律 404，URL 不触及路径。"""
    if not RUN_ID.match(run_id):
        return None
    path = _runs_root() / run_id / "journal.jsonl"
    return path if path.is_file() else None


def _after_seq(request: Request) -> int:
    header = request.headers.get("Last-Event-ID")
    if header and header.isdigit():
        return int(header)
    raw = request.query_params.get("after")
    return int(raw) if raw and raw.isdigit() else 0


def _run_info(path: Path) -> dict | None:
    """一次过去运行的一行摘要，全部来自它的日志。"""
    try:
        events = Journal.load(path / "journal.jsonl")
    except Exception:
        return None
    started = next((e for e in events if e.type == "run.started"), None)
    finished = next((e for e in events if e.type == "run.finished"), None)
    if started is None:
        return None
    request = started.data.get("request", {})
    return {"run_id": path.name, "ticker": request.get("ticker", ""),
            "as_of": request.get("as_of", ""),
            "status": finished.data.get("status", "进行中") if finished else "进行中",
            "reason": finished.data.get("reason", "") if finished else "",
            "mode": started.data.get("mode", "")}


def _scope_of(path: Path) -> tuple[dict | None, str]:
    try:
        events = Journal.load(path)
        started = next((e for e in events if e.type == "run.started"), None)
        if started is None:
            return None, ""
        scope = started.data.get("request", {})
        return scope, started.data.get("universe", "synthetic")
    except Exception:
        return None, ""


async def _stream(app: FastAPI, path: Path, run_id: str, after: int):
    """按事件逐条发送日志。读文件而不读活的 Run：过去与进行中的运行走同一条路。

    一行没有换行说明正在追加，忽略它；解析不了的行视为尚未写完，下轮再试。
    """
    sent = after
    quiet = 0
    while True:
        grew = False
        try:
            raw = await asyncio.to_thread(path.read_text, "utf-8")
        except FileNotFoundError:
            return
        lines = raw.split("\n")
        for line in lines[sent:]:
            if not line:
                continue
            try:
                event = Event.model_validate_json(line)
            except Exception:                    # 正在写的半行：留到下一轮
                grew = True
                break
            sent = event.seq
            grew = True
            payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            yield f"event: journal\nid: {event.seq}\ndata: {payload}\n\n"
            if event.type == "run.finished":
                yield 'event: end\ndata: {"error": null}\n\n'
                return
        if app.state.error.get(run_id):
            message = app.state.error[run_id]
            yield f'event: end\ndata: {json.dumps({"error": message}, ensure_ascii=False)}\n\n'
            return
        quiet = 0 if grew else quiet + 1
        if quiet > 10 and app.state.active != run_id:
            # 既不在运行、日志也不再增长、又没有收尾事件：运行没有正常结束。
            yield ('event: end\ndata: {"error": "日志缺少 run.finished（运行未正常收尾）"}\n\n')
            return
        await asyncio.sleep(0.2)


def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    """启动本地服务并打开页面。端口被占用时顺延到下一个空闲端口并打印实际地址。"""
    application = create_app(settings)
    with socket.socket() as probe:
        while True:
            try:
                probe.bind(("127.0.0.1", port))
                break
            except OSError:
                port += 1                                       # 端口被占用：顺延到下一个空闲端口
    address = f"http://127.0.0.1:{port}/"
    print(f"InsightCrew 本地服务：{address}（Ctrl-C 停止）")
    if open_browser:
        webbrowser.open(address)
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False, log_level="warning")


def _version() -> str:
    from insightcrew import __version__
    return __version__
