"""本地网页的测试：每条路由与状态码；经 HTTP 发起的脚本化运行与命令行给出同样的结果；
事件流每条事件只发一次并能续传；第二次启动得到 409；
错误的 Host、别处的 Origin、不合规的 run_id 与请求体被拒绝；任何回复都不含密钥与路径。"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insightcrew.app import server
from insightcrew.app.server import RUN_ID, create_app
from insightcrew.app.settings import Settings, load
from insightcrew.app.wiring import ROOT

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
                         "ceilings", "active_run"}
    assert body["mode"] == "scripted" and body["models"] == {"quick": "scripted", "deep": "scripted"}
    assert body["universe"] == "synthetic" and body["active_run"] is None
    acme = next(i for i in body["instruments"] if i["ticker"] == "ACME")
    assert acme == {"ticker": "ACME", "name": acme["name"], "market": "US", "currency": "USD",
                    "synthetic": True, "last_bar": "2026-09-30"}
    demo = next(s for s in body["scenarios"] if s["name"] == "demo")
    assert demo["ticker"] == "ACME" and demo["as_of"] == "2026-09-30" and demo["about"]
    assert body["defaults"] == {"judges": True, "debate_rounds": 1}


# ---- 发起运行，看着它结束 ----

def test_a_run_started_over_http_gives_what_the_command_line_gives(client: TestClient, tmp_path: Path) -> None:
    run_id = _start(client, scenario="demo")
    assert RUN_ID.fullmatch(run_id) and (tmp_path / run_id).is_dir()

    messages = _watch(client, run_id)
    journal = [(seq, data) for name, seq, data in messages if name == "journal"]
    assert [seq for seq, _ in journal] == list(range(1, len(journal) + 1))       # 每条事件恰好一次，按序
    assert all(seq == data["seq"] for seq, data in journal)
    assert journal[-1][1]["type"] == "run.finished"
    assert messages[-1] == ("end", 0, {"error": None})                           # 收尾消息只有一条，在最后
    assert [name for name, _, _ in messages].count("end") == 1
    assert _outcome(messages) == {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                                  "judgment": "accepted 2/3"}

    lines = (tmp_path / run_id / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert [data for _, data in journal] == [json.loads(line) for line in lines]  # 流里发的就是日志里写的

    listed = client.get("/api/runs").json()["runs"]
    assert listed == [{"run_id": run_id, "ticker": "ACME", "as_of": "2026-09-30", "status": "delivered",
                       "reason": "ok", "mode": "scripted"}]
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
    assert messages[-1] == ("end", 0, {"error": None})


def test_stream_resumes_after_the_last_event_seen(client: TestClient) -> None:
    run_id = _start(client, scenario="demo")
    total = len(_watch(client, run_id)) - 1
    assert total >= 30
    by_header = _watch(client, run_id, headers={"Last-Event-ID": "3"})
    assert [seq for name, seq, _ in by_header if name == "journal"] == list(range(4, total + 1))
    by_query = _watch(client, run_id, params={"after": str(total - 1)})
    assert [(name, seq) for name, seq, _ in by_query] == [("journal", total), ("end", 0)]
    assert _watch(client, run_id, params={"after": str(total)}) == [("end", 0, {"error": None})]


def test_second_start_gets_409(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    app.state.session.active = "20260101-000000-ACME-2026-09-30-abcd"      # 假装有一次运行正在进行
    response = client.post("/api/runs", json={"scenario": "demo"})
    assert response.status_code == 409
    assert response.json()["active_run"] == "20260101-000000-ACME-2026-09-30-abcd"
    assert client.get("/api/meta").json()["active_run"] == "20260101-000000-ACME-2026-09-30-abcd"
    assert list(tmp_path.iterdir()) == []


def test_an_unexpected_exception_ends_the_stream_with_an_error(client: TestClient, app: FastAPI,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "run_flow", broken)
    run_id = _start(client, scenario="demo")
    messages = _watch(client, run_id)
    assert messages[-1] == ("end", 0, {"error": "RuntimeError: boom"})
    assert app.state.session.active is None        # 无论怎样结束，都清掉"进行中"
    listed = client.get("/api/runs").json()["runs"]
    assert listed[0]["run_id"] == run_id and listed[0]["status"] == "incomplete"


def test_a_journal_that_never_finished_is_listed_and_streamed_as_incomplete(client: TestClient, tmp_path: Path) -> None:
    run_id = "20260101-000000-ACME-2026-09-30-abcd"
    (tmp_path / run_id).mkdir()
    started = {"seq": 1, "ts": "", "type": "run.started", "step": "", "actor": "app", "caused_by": [],
               "data": {"request": {"ticker": "ACME"}, "mode": "scripted", "universe": "synthetic"}}
    (tmp_path / run_id / "journal.jsonl").write_text(
        json.dumps(started) + "\n" + '{"seq": 2, "ts": "", "type": "model.ca', encoding="utf-8")   # 第二行没写完
    assert client.get("/api/runs").json()["runs"] == [
        {"run_id": run_id, "ticker": "ACME", "as_of": "2026-09-30", "status": "incomplete", "reason": "",
         "mode": "scripted"}]
    messages = _watch(client, run_id)
    assert [(name, seq) for name, seq, _ in messages] == [("journal", 1), ("end", 0)]
    assert "没有正常结束" in messages[-1][2]["error"]
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
    replies = [client.get(f"/api/runs/{run_id}/events").text, client.get("/api/meta").text,
               client.get("/api/runs").text, client.get(f"/api/runs/{run_id}/bars").text, client.get("/").text,
               client.post("/api/runs", json={"scenario": "nope"}).text]
    for text in replies:
        assert SECRET not in text and "secret-endpoint" not in text
        assert str(ROOT) not in text and str(tmp_path) not in text
    for response in (client.get("/api/meta"), client.get("/api/nothing"), client.post("/api/runs", json={"x": 1})):
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


# ---- 版本 2.2 的表单字段（BLUEPRINT 19.15）：组合文件、风险轮数、工具、记忆、新通道 ----


def _artifact(messages: list[tuple[str, int, Any]], key: str) -> Any:
    return next(data["data"]["value"] for name, _, data in messages
                if name == "journal" and data["type"] == "artifact.written" and data["data"]["key"] == key)


def test_the_form_passes_a_portfolio_file_through(client: TestClient) -> None:
    """组合文件经表单进入 open_run：组合阶段写下 portfolio/state，运行照常交付。"""
    messages = _watch(client, _start(client, scenario="clean", portfolio="data/portfolios/flat_usd.json"))
    assert _outcome(messages)["status"] == "delivered"
    state = _artifact(messages, "portfolio/state")
    assert state["state"] == "flat" and state["cash_pct"] == 100.0
    assert _artifact(messages, "portfolio/input")["currency"] == "USD"


def test_a_portfolio_file_that_cannot_be_read_is_refused_without_a_path(client: TestClient,
                                                                         tmp_path: Path) -> None:
    for portfolio in ("data/portfolios/no-such-file.json", "data/rules.toml"):
        response = client.post("/api/runs", json={"scenario": "demo", "portfolio": portfolio})
        assert response.status_code == 422, portfolio
        assert response.json()["error"] == "组合文件读不出来或不符合格式。"
    assert list(tmp_path.iterdir()) == []          # 拒绝发生在开运行之前，什么都没有创建


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
