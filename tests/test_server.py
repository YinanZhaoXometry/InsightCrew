"""本地网页的测试：每条路由与状态码；经 HTTP 发起的脚本化运行与命令行给出同样的结果；
事件流每条事件只发一次并能续传；第二次启动得到 409；
错误的 Host、别处的 Origin、不合规的 run_id 与请求体被拒绝；任何回复都不含密钥与路径。

版本 2.3（BLUEPRINT 20.8）：网页运行带检查点（D5）；取消与续跑；事件流带 trace 行与
keep-alive、end 报 finished/interrupted；报告与静态页导出；meta 扩展；组合按名字选；
计划预览。凡走续跑路径的测试都设 IC_FORCE_OFFLINE=1 并给 resume._models 换剧本
（问题 38：它不看 force_offline，不换会想对真实端点发请求）。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel

from insightcrew.adapters.scripted import Script
from insightcrew.app import resume as resume_cmd
from insightcrew.app import server, wiring
from insightcrew.app.server import RUN_ID, create_app
from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT
from insightcrew.evals.scenarios import load_one
from insightcrew.kernel.runtime import Role
from insightcrew.roles import LENSES

SECRET = "sk-secret-for-tests"
MESSAGE = re.compile(r"^event: (\w+)\n(?:id: (\d+)\n)?data: (.*)$", re.S)


@pytest.fixture
def settings() -> Settings:
    return load(ROOT, force_offline=True, api_key=SECRET, base_url="http://secret-endpoint.example/v1")


@pytest.fixture
def app(settings: Settings, tmp_path: Path) -> FastAPI:
    return create_app(settings, runs_dir=tmp_path)            # 测试的运行不进仓库的 runs/


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Host 必须是本机：服务器只接受 127.0.0.1 与 localhost（见 test_wrong_host_is_refused）。
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def _messages(text: str) -> list[tuple[str, int, Any]]:
    """事件流的文本拆成 (事件名, id, 数据)。"""
    out = []
    for block in text.split("\n\n"):
        if match := MESSAGE.match(block):
            out.append((match.group(1), int(match.group(2) or 0), json.loads(match.group(3))))
    return out


def _start(client: TestClient, **body: Any) -> str:
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _watch(client: TestClient, run_id: str, **kwargs: Any) -> list[tuple[str, int, Any]]:
    """读完一次运行的事件流。流在运行结束时才关闭，所以这也是在等运行结束。"""
    response = client.get(f"/api/runs/{run_id}/events", **kwargs)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return _messages(response.text)


def _outcome(messages: list[tuple[str, int, Any]]) -> dict[str, str]:
    return next(data["data"]["value"] for name, _, data in messages
                if name == "journal" and data["type"] == "artifact.written" and data["data"]["key"] == "run/outcome")


# ---- 页面与表单信息 ----

def test_page_is_served_in_live_mode(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<script type="application/json" id="embedded">null</script>' in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_meta_says_what_the_form_needs_and_nothing_more(client: TestClient) -> None:
    body = client.get("/api/meta").json()
    assert set(body) == {"version", "mode", "models", "universe", "instruments", "scenarios", "defaults",
                         "ceilings", "active_run", "channels", "portfolios", "limits"}
    assert body["mode"] == "scripted" and body["models"] == {"quick": "scripted", "deep": "scripted"}
    assert body["universe"] == "synthetic" and body["active_run"] is None
    acme = next(i for i in body["instruments"] if i["ticker"] == "ACME")
    assert acme == {"ticker": "ACME", "name": acme["name"], "market": "US", "currency": "USD",
                    "synthetic": True, "last_bar": "2026-09-30"}
    demo = next(s for s in body["scenarios"] if s["name"] == "demo")
    assert demo["ticker"] == "ACME" and demo["as_of"] == "2026-09-30" and demo["about"]
    assert body["defaults"] == {"judges": True, "debate_rounds": 1, "risk_rounds": 0, "tools": False,
                                "memory": False, "channels": []}          # 表单展示的每个流程字段（20.8 第 6 条）
    assert body["channels"] == ["fundamentals", "disclosures", "news", "sentiment", "macro"]
    assert body["portfolios"] == ["flat_usd", "held_acme", "low_cash_usd"]     # data/portfolios/ 的文件名
    assert body["limits"] == {"max_requests": 40, "max_seconds": 900.0}
    assert set(body["ceilings"]) == {"run_tokens", "run_cost", "roles"}       # 角色上限与运行上限并列


# ---- 发起运行，看着它结束 ----

def test_a_run_started_over_http_gives_what_the_command_line_gives(client: TestClient, tmp_path: Path) -> None:
    run_id = _start(client, scenario="demo")
    assert RUN_ID.fullmatch(run_id) and (tmp_path / run_id).is_dir()

    messages = _watch(client, run_id)
    journal = [(seq, data) for name, seq, data in messages if name == "journal"]
    assert [seq for seq, _ in journal] == list(range(1, len(journal) + 1))       # 每条事件只发一次，按序
    assert all(seq == data["seq"] for seq, data in journal)
    assert journal[-1][1]["type"] == "run.finished"
    assert messages[-1] == ("end", 0, {"error": None, "status": "finished"})     # 收尾消息只有一条，在最后
    assert [name for name, _, _ in messages].count("end") == 1
    assert _outcome(messages) == {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                                  "judgment": "accepted 2/3"}

    lines = (tmp_path / run_id / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert [data for _, data in journal] == [json.loads(line) for line in lines]  # 流里发的就是日志里写的

    listed = client.get("/api/runs").json()["runs"]
    assert listed == [{"run_id": run_id, "ticker": "ACME", "as_of": "2026-09-30", "status": "delivered",
                       "reason": "ok", "mode": "scripted", "resumable": False}]
    assert client.get("/api/meta").json()["active_run"] is None

    deadline = time.monotonic() + 10                # 运行页面在 run.finished 之后才写
    while not (tmp_path / run_id / "replay.html").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (tmp_path / run_id / "replay.html").exists()


def test_the_form_can_override_the_flow(client: TestClient) -> None:
    messages = _watch(client, _start(client, scenario="clean", judges=False, debate_rounds=0))
    actors = [data["actor"] for name, _, data in messages if name == "journal" and data["type"] == "model.called"]
    assert actors == ["technical", "manager", "trader"]
    assert _outcome(messages)["judgment"] == "not reviewed: judges disabled"


def test_intake_refusals_end_as_they_always_do(client: TestClient) -> None:
    thin = _watch(client, _start(client, ticker="THIN"))
    assert _outcome(thin)["reason"] == "insufficient_history"
    assert not [1 for name, _, data in thin if name == "journal" and data["type"] == "model.called"]

    run_id = _start(client, ticker="BRK.B")         # 不合规的代码也走准入：REVIEW，而不是一个错误
    assert "-INVALID-" in run_id
    assert _outcome(_watch(client, run_id))["reason"] == "invalid_ticker"


def test_without_a_scenario_or_a_model_the_run_ends_as_review(client: TestClient) -> None:
    messages = _watch(client, _start(client, ticker="ACME"))
    assert _outcome(messages)["reason"] == "model_unavailable"
    assert messages[-1] == ("end", 0, {"error": None, "status": "finished"})


def test_stream_resumes_after_the_last_event_seen(client: TestClient) -> None:
    run_id = _start(client, scenario="demo")
    journal_seqs = [seq for name, seq, _ in _watch(client, run_id) if name == "journal"]
    total = len(journal_seqs)
    assert total >= 30
    by_header = _watch(client, run_id, headers={"Last-Event-ID": "3"})
    assert [seq for name, seq, _ in by_header if name == "journal"] == list(range(4, total + 1))
    by_query = _watch(client, run_id, params={"after": str(total - 1)})
    assert [(name, seq) for name, seq, _ in by_query if name != "trace"] == \
        [("journal", total), ("end", 0)]                    # 轨迹行没有 id，重连时整份重发，不参与续传
    tail = _watch(client, run_id, params={"after": str(total)})
    assert [(name, data) for name, _, data in tail if name == "end"] == \
        [("end", {"error": None, "status": "finished"})]


def test_second_start_gets_409(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    app.state.session.active = "20260101-000000-ACME-2026-09-30-abcd"      # 假装有一次运行正在进行
    response = client.post("/api/runs", json={"scenario": "demo"})
    assert response.status_code == 409
    assert response.json()["active_run"] == "20260101-000000-ACME-2026-09-30-abcd"
    assert client.get("/api/meta").json()["active_run"] == "20260101-000000-ACME-2026-09-30-abcd"
    assert list(tmp_path.iterdir()) == []


def test_an_unexpected_exception_ends_the_stream_with_an_error(client: TestClient, app: FastAPI,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "run_flow", broken)
    run_id = _start(client, scenario="demo")
    messages = _watch(client, run_id)
    assert messages[-1] == ("end", 0, {"error": "RuntimeError: boom", "status": "interrupted"})
    assert app.state.session.active is None        # 无论怎样结束，都清掉"进行中"
    listed = client.get("/api/runs").json()["runs"]
    assert listed[0]["run_id"] == run_id and listed[0]["status"] == "incomplete"
    assert listed[0]["resumable"] is False         # 模型流程被换掉了：没有检查点，不能续跑


def test_a_journal_that_never_finished_is_listed_and_streamed_as_incomplete(client: TestClient, tmp_path: Path) -> None:
    run_id = "20260101-000000-ACME-2026-09-30-abcd"
    (tmp_path / run_id).mkdir()
    started = {"seq": 1, "ts": "", "type": "run.started", "step": "", "actor": "app", "caused_by": [],
               "data": {"request": {"ticker": "ACME"}, "mode": "scripted", "universe": "synthetic"}}
    (tmp_path / run_id / "journal.jsonl").write_text(
        json.dumps(started) + "\n" + '{"seq": 2, "ts": "", "type": "model.ca', encoding="utf-8")   # 第二行没写完
    assert client.get("/api/runs").json()["runs"] == [
        {"run_id": run_id, "ticker": "ACME", "as_of": "2026-09-30", "status": "incomplete", "reason": "",
         "mode": "scripted", "resumable": False}]          # 2.2 之前的运行：没有签名，不能续跑
    messages = _watch(client, run_id)
    assert [(name, seq) for name, seq, _ in messages] == [("journal", 1), ("end", 0)]
    assert "没有正常结束" in messages[-1][2]["error"]
    assert messages[-1][2]["status"] == "interrupted"
    assert client.get(f"/api/runs/{run_id}/bars").json() == {"currency": "", "bars": []}


def test_past_runs_is_empty_when_there_is_no_run_directory(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings, runs_dir=tmp_path / "missing"), base_url="http://127.0.0.1") as fresh:
        assert fresh.get("/api/runs").json() == {"runs": []}


# ---- 价格序列 ----

def test_bars_are_the_closes_up_to_the_analysis_date(client: TestClient) -> None:
    run_id = _start(client, ticker="ACME", as_of="2026-06-30")
    _watch(client, run_id)
    body = client.get(f"/api/runs/{run_id}/bars").json()
    assert body["currency"] == "USD"
    assert len(body["bars"]) == 250 and set(body["bars"][0]) == {"date", "close"}
    assert body["bars"][-1]["date"] == "2026-06-30"
    assert [bar["date"] for bar in body["bars"]] == sorted(bar["date"] for bar in body["bars"])


def test_a_refused_run_has_no_bars(client: TestClient) -> None:
    run_id = _start(client, ticker="NOPE")
    _watch(client, run_id)
    assert client.get(f"/api/runs/{run_id}/bars").json() == {"currency": "", "bars": []}


# ---- 拒绝 ----

def test_wrong_host_is_refused(app: FastAPI) -> None:
    with TestClient(app, base_url="http://evil.example") as stranger:
        assert stranger.get("/api/meta").status_code == 400
        assert stranger.post("/api/runs", json={"scenario": "demo"}).status_code == 400


def test_foreign_origin_is_refused_and_the_page_itself_is_not(client: TestClient, tmp_path: Path) -> None:
    for origin in ("http://evil.example", "http://127.0.0.1:9999", "https://127.0.0.1", "null"):
        response = client.post("/api/runs", json={"scenario": "demo"}, headers={"Origin": origin})
        assert response.status_code == 403, origin
    assert list(tmp_path.iterdir()) == []
    own = client.post("/api/runs", json={"scenario": "thin"}, headers={"Origin": "http://127.0.0.1"})
    assert own.status_code == 202
    _watch(client, own.json()["run_id"])


def test_bad_run_id_is_refused(client: TestClient) -> None:
    for run_id in ("not-a-run", "..%2F..%2Fetc%2Fpasswd", "20260101-000000-ACME-2026-09-30-ZZZZ",
                   "20260101-000000-ACME-2026-09-30-abcd"):                 # 最后一个形状对，但没有这个目录
        assert client.get(f"/api/runs/{run_id}/events").status_code == 404, run_id
        assert client.get(f"/api/runs/{run_id}/bars").status_code == 404, run_id
    assert client.get("/api/runs/../../etc/passwd/events").status_code == 404


def test_malformed_body_is_refused_and_nothing_is_created(client: TestClient, tmp_path: Path) -> None:
    for body in ({"ticker": 123}, {"what": 1}, {"ticker": "A" * 33}, {"debate_rounds": 99}, {"judges": "maybe"},
                 {"scenario": "no-such-scenario"}, {"ticker": "ACME", "as_of": "../../x"},
                 {"ticker": "ACME", "as_of": "2026-13-45"}, [1, 2]):
        assert client.post("/api/runs", json=body).status_code == 422, body
    assert client.post("/api/runs", content="not-json",
                       headers={"Content-Type": "application/json"}).status_code == 422
    assert "无法解析" in client.post("/api/runs", json={"ticker": "ACME", "as_of": "2026-13-45"}).json()["error"]
    assert list(tmp_path.iterdir()) == []


def test_everything_else_is_404(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json", "/api", "/api/runs/x", "/journal.jsonl"):
        assert client.get(path).status_code == 404, path
    assert client.delete("/api/runs").status_code == 405


def test_no_reply_contains_the_key_the_endpoint_or_a_path(client: TestClient, tmp_path: Path) -> None:
    run_id = _start(client, scenario="demo")
    _watch(client, run_id)
    replies = [client.get(f"/api/runs/{run_id}/events").text, client.get("/api/meta").text,
               client.get("/api/runs").text, client.get(f"/api/runs/{run_id}/bars").text, client.get("/").text,
               client.post("/api/runs", json={"scenario": "nope"}).text,
               client.post("/api/plan", json={"ticker": "ACME"}).text,
               client.post("/api/plan", json={"ticker": "ACME", "as_of": "坏日期"}).text,
               client.post(f"/api/runs/{run_id}/cancel").text, client.post(f"/api/runs/{run_id}/resume").text,
               client.get(f"/api/runs/{run_id}/report").text, client.get(f"/api/runs/{run_id}/replay").text]
    for text in replies:
        assert SECRET not in text and "secret-endpoint" not in text
        assert str(ROOT) not in text and str(tmp_path) not in text
    for response in (client.get("/api/meta"), client.get("/api/nothing"), client.post("/api/runs", json={"x": 1}),
                     client.post("/api/plan", json={"ticker": "ACME"}),
                     client.get(f"/api/runs/{run_id}/report")):
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


# ---- 版本 2.2 的表单字段（BLUEPRINT 19.15）：组合文件、风险轮数、工具、记忆、新通道 ----


def _artifact(messages: list[tuple[str, int, Any]], key: str) -> Any:
    return next(data["data"]["value"] for name, _, data in messages
                if name == "journal" and data["type"] == "artifact.written" and data["data"]["key"] == key)


def test_the_form_passes_a_portfolio_file_through(client: TestClient) -> None:
    """组合按 meta.portfolios 里的名字经表单进入 open_run：组合阶段写下 portfolio/state，运行照常交付。"""
    messages = _watch(client, _start(client, scenario="clean", portfolio="flat_usd"))
    assert _outcome(messages)["status"] == "delivered"
    state = _artifact(messages, "portfolio/state")
    assert state["state"] == "flat" and state["cash_pct"] == 100.0
    assert _artifact(messages, "portfolio/input")["currency"] == "USD"


def test_the_portfolio_field_takes_a_known_name_only(client: TestClient, tmp_path: Path) -> None:
    """20.8 第 7 条：组合必须是 meta.portfolios 里的一个名字，或留空；别的一律 422，
    页面从此不能点名服务器上的任意路径（老写法 data/portfolios/flat_usd.json 也不再接受）。"""
    for portfolio in ("data/portfolios/flat_usd.json", "no-such-file", "../rules.toml", "flat_usd.json"):
        response = client.post("/api/runs", json={"scenario": "clean", "portfolio": portfolio})
        assert response.status_code == 422, portfolio
        assert "组合" in response.json()["error"]
        assert str(ROOT) not in response.text        # 拒绝的理由里没有路径
    run_id = _start(client, scenario="clean")        # 留空照常
    _watch(client, run_id)
    assert [p.name for p in tmp_path.iterdir()] == [run_id]     # 拒绝发生在开运行之前，什么都没有创建


def test_the_form_passes_the_new_flow_switches_through(client: TestClient) -> None:
    """风险轮数、工具、记忆与通道经表单进入 prepare：记入 run.started 的流程配置，
    证据与记忆阶段写下各自的键（ACME 的合成新闻与宏观序列在 2026-09-30 可读）。"""
    messages = _watch(client, _start(client, ticker="ACME", risk_rounds=2, tools=True, memory=True,
                                     channels=["news", "macro"]))
    assert _outcome(messages)["reason"] == "model_unavailable"     # 无场景也没有真实模型：到第一次模型调用停下
    started = next(data["data"] for name, _, data in messages
                   if name == "journal" and data["type"] == "run.started")
    assert started["flow"]["risk_rounds"] == 2 and started["flow"]["tools"] is True
    assert started["flow"]["memory"] is True
    assert tuple(started["flow"]["channels"]) == ("news", "macro")
    assert _artifact(messages, "news/status")["channel"] == "news"
    assert _artifact(messages, "news/status")["state"] == "available"
    assert _artifact(messages, "macro/status")["state"] == "available"
    assert isinstance(_artifact(messages, "memory/lessons"), list)


def test_the_new_form_fields_are_validated(client: TestClient, tmp_path: Path) -> None:
    for body in ({"ticker": "ACME", "risk_rounds": 99}, {"ticker": "ACME", "channels": "news"},
                 {"ticker": "ACME", "portfolio": "x" * 257}, {"ticker": "ACME", "memory": 3},
                 {"ticker": "ACME", "channels": ["nope"]}, {"ticker": "ACME", "channels": ["macro"]}):
        response = client.post("/api/runs", json=body)
        assert response.status_code == 422, body
    error = client.post("/api/runs", json={"ticker": "ACME", "channels": ["macro"]}).json()["error"]
    assert "宏观通道（macro）需要与 news 一起点名" in error
    named = client.post("/api/runs", json={"ticker": "ACME", "channels": ["nope"]}).json()["error"]
    assert "未知的证据通道：nope" in named
    assert list(tmp_path.iterdir()) == []


# ---- 版本 2.3 的运行控制与服务器 API（BLUEPRINT 20.8）----
#
# 本包的第一个测试是取消（HANDOFF 第 7 节的告诫：取消留下可续跑的检查点这件事
# 从来没有被试过——先写它、先见它红，不灵就停下来把所见记进第 7 节）。


class _WaitingScript:
    """剧本模型端口，但 technical 的调用先等一个事件：模拟一次慢调用，取消正好落在它头上。
    事件在取消测试里永远不会来；loop 记下来是给测试兜底用（万一取消打断不了在飞的调用，
    测试可以从别的线程把事件放开，免得整条测试挂死）。"""

    def __init__(self, replies: dict[str, list[Any]], gate: asyncio.Event) -> None:
        self._script, self._gate = Script(replies), gate
        self.loop: asyncio.AbstractEventLoop | None = None

    def for_role(self, role: Role) -> Model:
        if role.name != "technical":
            return self._script.for_role(role)

        async def respond(_messages: object, _info: object) -> ModelResponse:
            self.loop = asyncio.get_running_loop()
            await self._gate.wait()
            raise RuntimeError("等待中的调用不该被回答")

        return FunctionModel(respond)


def _events_of(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "journal.jsonl").read_text(encoding="utf-8").splitlines()]


def _trace_of(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


def _started_of(messages: list[tuple[str, int, Any]]) -> dict[str, Any]:
    return next(data["data"] for name, _, data in messages
                if name == "journal" and data["type"] == "run.started")


def _until_idle(client: TestClient) -> None:
    """等运行任务真正收尾（活动位清掉）：事件流在 run.finished 就收，任务还有一点尾工。"""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get("/api/meta").json()["active_run"] is None:
            return
        time.sleep(0.05)
    pytest.fail("运行任务没有在时限内收尾")


def test_a_cancelled_scripted_run_is_interrupted_and_resumes_to_delivered(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """20.8 第 2 条的主测试：取消一次进行中的脚本化运行——日志没有 run.finished、
    轨迹以 run.cancelled 收尾、没有进行中的运行；然后它从检查点续跑到 delivered。"""
    monkeypatch.setenv("IC_FORCE_OFFLINE", "1")    # 问题 38：续跑路径一律带上
    gate = asyncio.Event()
    waiting = _WaitingScript(load_one("demo").replies, gate)
    real_open = server.open_run

    def open_with_a_waiting_model(settings: Settings, request: dict[str, Any],
                                  **kwargs: Any) -> tuple[Any, Any]:
        run, deps = real_open(settings, request, **kwargs)
        run.models = waiting
        return run, deps

    monkeypatch.setattr(server, "open_run", open_with_a_waiting_model)
    run_id = _start(client, scenario="demo")
    run_dir = tmp_path / run_id

    deadline = time.monotonic() + 10               # 等第一次模型调用真的在飞（轨迹里出现 role.started）
    while time.monotonic() < deadline:
        trace = run_dir / "trace.jsonl"
        if trace.is_file() and '"role.started"' in trace.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("运行没有走到第一次模型调用")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:   # 取消必须打断在飞的调用，
        future = pool.submit(client.post, f"/api/runs/{run_id}/cancel")  # 所以它可能挂住：兜底见下
        try:
            reply = future.result(timeout=20)
        except concurrent.futures.TimeoutError:
            assert waiting.loop is not None
            waiting.loop.call_soon_threadsafe(gate.set)                 # 放开在飞的调用，让请求能回来
            reply = future.result(timeout=20)
            pytest.fail(f"取消没有打断在飞的模型调用：POST cancel 挂到模型返回才回来"
                        f"（{reply.status_code} {reply.text}）")
    assert reply.status_code == 202 and reply.json() == {"run_id": run_id}

    assert all(event["type"] != "run.finished" for event in _events_of(run_dir))   # 日志不写取消
    assert (run_dir / "checkpoints.sqlite").is_file()                              # 检查点还在
    trace = _trace_of(run_dir)
    assert trace[-1]["kind"] == "run.cancelled"                                    # 轨迹记下取消
    assert client.get("/api/meta").json()["active_run"] is None                    # 没有进行中的运行
    listed = client.get("/api/runs").json()["runs"]
    assert listed[0]["status"] == "incomplete" and listed[0]["resumable"] is True

    monkeypatch.setattr(resume_cmd, "_models",                     # 问题 38：_models 不看 force_offline，
                        lambda s, e: Script(load_one("demo").replies))   # 测试里换成剧本，另以环境变量兜底
    reply = client.post(f"/api/runs/{run_id}/resume")
    assert reply.status_code == 202 and reply.json() == {"run_id": run_id}
    messages = _watch(client, run_id)                              # 看着续跑跑完
    assert _outcome(messages) == {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                                  "judgment": "accepted 2/3"}
    assert _events_of(run_dir)[-1]["type"] == "run.finished"
    assert "run.resumed" in [line["kind"] for line in _trace_of(run_dir)]   # 续跑在轨迹里记了一行
    assert client.get("/api/runs").json()["runs"][0]["resumable"] is False


def test_a_run_started_over_http_leaves_a_checkpoint_and_a_trace(client: TestClient,
                                                                 tmp_path: Path) -> None:
    """D5（20.8 第 1 条）：网页发起的运行走 wiring.run_flow——留检查点与 trace，
    这样它才续跑得了；轨迹的 role.started/role.finished 成对。"""
    run_id = _start(client, scenario="demo")
    _watch(client, run_id)
    run_dir = tmp_path / run_id
    assert (run_dir / "checkpoints.sqlite").is_file()
    trace = _trace_of(run_dir)
    starts = [line["step"] for line in trace if line["kind"] == "role.started"]
    ends = [line["step"] for line in trace if line["kind"] == "role.finished"]
    assert starts and starts == ends                              # 每次调用有始有终（20.6 的行形状）


def test_cancel_refuses_a_run_that_is_not_active(client: TestClient) -> None:
    """取消只对进行中的运行成立：已结束 409；没有这个运行 404；进行中的是另一个 409。"""
    finished = _start(client, scenario="demo")
    _watch(client, finished)
    _until_idle(client)                                        # 任务收尾后才算"已结束"
    reply = client.post(f"/api/runs/{finished}/cancel")
    assert reply.status_code == 409 and "取消" in reply.json()["error"]

    other = _start(client, scenario="clean")
    app_session = client.app.state.session                        # type: ignore[union-attr]
    app_session.active = "20260101-000000-ACME-2026-09-30-abcd"   # 假装活动运行是另一个
    reply = client.post(f"/api/runs/{other}/cancel")
    assert reply.status_code == 409 and reply.json()["active_run"] == "20260101-000000-ACME-2026-09-30-abcd"
    app_session.active = other
    _watch(client, other)                                         # 让门里的运行收尾，别把活动位留脏

    assert client.post("/api/runs/20260101-000000-ACME-2026-09-30-abcd/cancel").status_code == 404
    assert client.post("/api/runs/not-a-run/cancel").status_code == 404


def _crashed_run(settings: Settings, runs_dir: Path, portfolio: str = "") -> Path:
    """一个真的中断了的运行（与命令行测试同一手法）：demo 跑到交易员之前崩，
    日志没有 run.finished，检查点留在 manager。"""
    scenario = load_one("demo")
    job = wiring.prepare(settings, scenario="demo", portfolio=portfolio)
    run, deps = wiring.open_run(settings, job.request, scenario=scenario, flow=job.flow,
                                runs_dir=runs_dir, portfolio=job.portfolio_path)

    class _CrashOn:
        def __init__(self, replies: dict[str, list[Any]], crash_on: str) -> None:
            self._script, self._crash_on, self._armed = Script(replies), crash_on, True

        def for_role(self, role: Role) -> Model:
            if role.name == self._crash_on and self._armed:
                self._armed = False
                raise RuntimeError("模拟中断")
            return self._script.for_role(role)

    run.models = _CrashOn(scenario.replies, "trader")
    with pytest.raises(RuntimeError):
        asyncio.run(wiring.run_flow(run, job.request, deps, job.flow))
    assert deps.out_dir is not None
    return deps.out_dir


def test_resume_refuses_what_it_cannot_continue(settings: Settings, client: TestClient,
                                                tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """20.8 第 3 条的拒绝：已结束、签名不一致、版本 2.2 之前、没有检查点、另有运行在进行、
    没有这个运行；理由是中文，落在 409 的回复里。签名不一致以带组合的运行触发：续跑重算
    签名时不复原组合（resume 的既有读法，见会话记录），指纹必不相同。"""
    monkeypatch.setenv("IC_FORCE_OFFLINE", "1")                   # 本测试走 resume_run 的拒绝前缀
    finished = _start(client, scenario="demo")
    _watch(client, finished)
    _until_idle(client)
    reply = client.post(f"/api/runs/{finished}/resume")
    assert reply.status_code == 409 and "已经结束" in reply.json()["error"]

    crashed = _crashed_run(settings, tmp_path, portfolio="data/portfolios/flat_usd.json")
    reply = client.post(f"/api/runs/{crashed.name}/resume")
    assert reply.status_code == 409 and "运行签名不一致" in reply.json()["error"]

    old = tmp_path / "20260101-000000-ACME-2026-09-30-abbe"       # 2.2 之前的运行：日志里没有签名
    old.mkdir()
    started = {"seq": 1, "ts": "", "type": "run.started", "step": "", "actor": "app", "caused_by": [],
               "data": {"request": {"ticker": "ACME"}, "mode": "scripted", "universe": "synthetic",
                        "flow": {}, "models": {"quick": "scripted", "deep": "scripted"}}}
    (old / "journal.jsonl").write_text(json.dumps(started) + "\n", encoding="utf-8")
    (old / "checkpoints.sqlite").write_bytes(b"")                 # 结构检查放行，让 resume 自己给理由
    reply = client.post("/api/runs/20260101-000000-ACME-2026-09-30-abbe/resume")
    assert reply.status_code == 409 and "版本 2.2" in reply.json()["error"]

    no_checkpoint = tmp_path / "20260101-000001-ACME-2026-09-30-abcd"
    no_checkpoint.mkdir()
    (no_checkpoint / "journal.jsonl").write_text(json.dumps(
        {**started, "data": {**started["data"], "thread": "0123456789abcdef"}}) + "\n",
        encoding="utf-8")
    reply = client.post("/api/runs/20260101-000001-ACME-2026-09-30-abcd/resume")
    assert reply.status_code == 409 and "检查点" in reply.json()["error"]

    client.app.state.session.active = "20260101-000000-ACME-2026-09-30-abcd"     # type: ignore[union-attr]
    reply = client.post(f"/api/runs/{crashed.name}/resume")
    assert reply.status_code == 409 and "正在进行" in reply.json()["error"]
    client.app.state.session.active = None                        # type: ignore[union-attr]

    assert client.post("/api/runs/20260101-000000-ACME-2026-09-30-abcd/resume").status_code == 404


def test_the_stream_carries_trace_events_and_the_end_says_finished(client: TestClient) -> None:
    """20.8 第 4 条：事件流把每条轨迹行作为 event: trace 送上（没有 id）；重连时整份重发；
    end 的数据带 status（finished/interrupted）。"""
    run_id = _start(client, scenario="demo")
    messages = _watch(client, run_id)
    trace = [data for name, _, data in messages if name == "trace"]
    kinds = {line["kind"] for line in trace}
    assert {"role.started", "role.finished"} <= kinds
    assert all({"ts", "kind"} <= set(line) for line in trace)
    total = len([1 for name, _, _ in messages if name == "journal"])
    assert messages[-1] == ("end", 0, {"error": None, "status": "finished"})

    again = _watch(client, run_id, params={"after": str(total)})   # 重连：journal 续传，trace 整份重发
    assert [data for name, _, data in again if name == "trace"] == trace
    assert again[-1] == ("end", 0, {"error": None, "status": "finished"})


def test_exports_serve_the_report_and_the_replay(client: TestClient, tmp_path: Path) -> None:
    """20.8 第 5、10 条：报告与静态页作为附件导出（404 当文件不存在）；
    write_page 把轨迹嵌进静态页。"""
    run_id = _start(client, scenario="demo")
    _watch(client, run_id)
    report = client.get(f"/api/runs/{run_id}/report")
    assert report.status_code == 200
    assert report.headers["content-type"] == "text/markdown; charset=utf-8"
    assert report.headers["content-disposition"].startswith("attachment")
    assert "评级" in report.text

    replay = client.get(f"/api/runs/{run_id}/replay")
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/html")
    assert replay.headers["content-disposition"].startswith("attachment")
    assert 'id="embedded"' in replay.text and '"trace": [' in replay.text    # 轨迹进了静态页

    for probe in (client.head(f"/api/runs/{run_id}/report"),
                  client.head(f"/api/runs/{run_id}/replay")):   # 页面用 HEAD 探测，链接才显示（20.10）
        assert probe.status_code == 200 and not probe.content

    bare = tmp_path / "20260101-000000-ACME-2026-09-30-abcd"       # 只有日志的运行：两个文件都没有
    bare.mkdir()
    (bare / "journal.jsonl").write_text("{}\n", encoding="utf-8")
    assert client.get("/api/runs/20260101-000000-ACME-2026-09-30-abcd/report").status_code == 404
    assert client.get("/api/runs/20260101-000000-ACME-2026-09-30-abcd/replay").status_code == 404
    assert client.get("/api/runs/not-a-run/report").status_code == 404


def test_channels_null_means_the_settings_and_an_empty_tuple_means_none(
        settings: Settings, tmp_path: Path) -> None:
    """20.8 第 7 条：channels 是 tuple | None——None（或不给）用设置里的 [flow] channels，
    空数组表示一个也不开，非空的数组才是这一次运行的显式选择。"""
    configured = load(ROOT, force_offline=True, api_key=SECRET,
                      base_url="http://secret-endpoint.example/v1", flow={"channels": ["fundamentals"]})
    with TestClient(create_app(configured, runs_dir=tmp_path), base_url="http://127.0.0.1") as fresh:
        by_settings = _watch(fresh, _start(fresh, ticker="ACME"))
        assert tuple(_started_of(by_settings)["flow"]["channels"]) == ("fundamentals",)
        none_at_all = _watch(fresh, _start(fresh, ticker="ACME", channels=[]))
        assert tuple(_started_of(none_at_all)["flow"]["channels"]) == ()
        explicit = _watch(fresh, _start(fresh, ticker="ACME", channels=["news", "macro"]))
        assert tuple(_started_of(explicit)["flow"]["channels"]) == ("news", "macro")


def test_the_plan_preview_estimates_without_running(client: TestClient, tmp_path: Path) -> None:
    """20.8 第 8 条：POST /api/plan 不调模型、不建运行目录——阶段与角色（分析师标并行与否）、
    典型与上限调用、token 与成本上限、每条通道 off/可用/不可用带原因、组合状态；prepare 拒绝的请求 422。"""
    reply = client.post("/api/plan", json={"ticker": "ACME", "as_of": "2026-09-30",
                                           "judges": True, "debate_rounds": 1})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["ticker"] == "ACME" and body["as_of"] == "2026-09-30" and body["mode"] == "scripted"
    stages = {stage["key"]: stage for stage in body["stages"]}
    assert list(stages) == ["ledger", "evidence", "technical", "debate", "manager", "trader",
                            "audit", "judges", "report"]
    assert stages["technical"]["parallel"] is True and stages["technical"]["roles"] == ["technical"]
    assert stages["debate"]["roles"] == ["bull", "bear"]
    assert stages["judges"]["roles"] == ["judge.evidence", "judge.conservative", "judge.aggressive"]
    assert all(stage["parallel"] is False for key, stage in stages.items() if key != "technical")
    assert body["calls"]["ceiling"] == 40                                     # limits.max_requests
    assert body["calls"]["typical"] == 1 + 2 + 1 + 1 + len(LENSES)           # 无返工的调用量（评审见第 7 节的读法）
    assert set(body["ceilings"]) == {"run_tokens", "run_cost"}
    assert all(info["state"] == "off" for info in body["channels"].values())  # 没点名 = 关
    assert body["portfolio"]["state"] == "not_provided"
    assert list(tmp_path.iterdir()) == []                                    # 没有运行目录

    covered = client.post("/api/plan", json={"ticker": "ACME",
                                             "channels": ["news", "macro"]}).json()["channels"]
    assert covered["news"]["state"] == "available"
    assert covered["macro"]["state"] == "available"
    assert covered["fundamentals"]["state"] == "off"

    hk = client.post("/api/plan", json={"ticker": "0000.HK", "as_of": "2026-06-30",
                                        "channels": ["fundamentals"]}).json()["channels"]
    assert hk["fundamentals"]["state"] == "unavailable" and hk["fundamentals"]["reason"]

    plan = client.post("/api/plan", json={"scenario": "demo"}).json()         # 场景的请求与流程照算
    assert plan["ticker"] == "ACME" and plan["as_of"] == "2026-09-30"
    assert list(tmp_path.iterdir()) == []

    for bad in ({"ticker": "ACME", "as_of": "2026-13-45"}, {"ticker": "ACME", "channels": ["nope"]},
                {"ticker": "ACME", "channels": ["macro"]}, {"scenario": "no-such-scenario"},
                {"portfolio": "no-such-file"}):
        assert client.post("/api/plan", json=bad).status_code == 422, bad
    assert list(tmp_path.iterdir()) == []
