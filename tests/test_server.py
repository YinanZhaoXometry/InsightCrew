"""本地网页的测试：每条路由与状态码、事件流、409、Host/Origin/run_id/请求体的拒绝、密钥不出现在回复里。"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insightcrew.app.server import create_app
from insightcrew.app.settings import load
from insightcrew.app.wiring import ROOT

RUN_ID_SHAPE = re.compile(r"^[0-9]{8}-[0-9]{6}-[A-Za-z0-9_.]+-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{4}$")
EVENT_ID = re.compile(r"^id: (\d+)$", re.M)


def _seqs(sse_text: str) -> list[int]:
    """从事件流文本里按顺序取出每条 journal 消息的 id。"""
    return [int(match.group(1)) for m in sse_text.split("\n\n")
            if m.startswith("event: journal") and (match := EVENT_ID.search(m))]


@pytest.fixture
def settings():
    return load(ROOT, force_offline=True, api_key="sk-secret-for-tests")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # Host 必须是本机：服务器只接受 127.0.0.1 / localhost（见 test_wrong_host_is_refused）。
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_page_is_live_mode(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<script type="application/json" id="embedded">null</script>' in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_meta_describes_scripted_mode(client) -> None:
    body = client.get("/api/meta").json()
    assert body["mode"] == "scripted"
    assert body["models"] == {"quick": "scripted", "deep": "scripted"}
    assert body["universe"] == "synthetic"
    assert any(i["ticker"] == "ACME" for i in body["instruments"])
    assert any(s["name"] == "demo" for s in body["scenarios"])
    assert "sk-secret-for-tests" not in str(body)          # 密钥不进任何回复


def test_start_run_and_watch_it_finish(client) -> None:
    started = client.post("/api/runs", json={"scenario": "demo"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert RUN_ID_SHAPE.match(run_id)

    stream = client.get(f"/api/runs/{run_id}/events")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    messages = [m for m in stream.text.split("\n\n") if m.strip()]
    ends = [m for m in messages if m.startswith("event: end")]
    journals = [m for m in messages if m.startswith("event: journal")]
    assert len(ends) == 1 and 'error":null' in ends[0].replace(" ", "")
    seqs = _seqs(stream.text)
    assert seqs == sorted(set(seqs))                       # 每条事件恰好一次，按序
    assert any('"type":"run.finished"' in m.replace(" ", "") for m in journals)

    runs = client.get("/api/runs").json()["runs"]
    assert any(r["run_id"] == run_id and r["status"] == "delivered" for r in runs)


def test_stream_resumes_after_last_event_id(client) -> None:
    started = client.post("/api/runs", json={"scenario": "demo"})
    run_id = started.json()["run_id"]
    full = client.get(f"/api/runs/{run_id}/events").text
    total = len(_seqs(full))
    assert total >= 10                                     # demo 一次至少十来条事件
    tail = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "3"}).text
    resumed = _seqs(tail)
    assert resumed[0] == 4                                 # 从第 4 条继续，不重复已收到的
    assert len(resumed) == total - 3


def test_second_start_gets_409(app, client) -> None:
    app.state.active = "20260101-000000-ACME-2026-09-30-abcd"      # 假装有一个运行在进行
    response = client.post("/api/runs", json={"scenario": "demo"})
    assert response.status_code == 409
    assert response.json()["active_run"] == app.state.active


def test_wrong_host_is_refused(settings) -> None:
    with TestClient(create_app(settings), base_url="http://evil.example") as stranger:
        assert stranger.get("/api/meta").status_code == 400


def test_foreign_origin_is_refused(client) -> None:
    response = client.post("/api/runs", json={"scenario": "demo"},
                           headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_bad_run_id_is_refused(client) -> None:
    assert client.get("/api/runs/not-a-run/events").status_code == 404
    assert client.get("/api/runs/../../etc/passwd/events").status_code == 404
    assert client.get("/api/runs/20260101-000000-ACME-2026-09-30-ZZZZ/events").status_code == 404
    assert client.get("/api/runs/20260101-000000-ACME-2026-09-30-abcd/bars").status_code == 404


def test_malformed_body_is_refused(client) -> None:
    assert client.post("/api/runs", json={"ticker": 123}).status_code == 422       # 类型不对
    assert client.post("/api/runs", json={"what": 1}).status_code == 422           # 未知的字段
    assert client.post("/api/runs", json={"scenario": "no-such-scenario"}).status_code == 422
    assert client.post("/api/runs", content="not-json",
                       headers={"Content-Type": "application/json"}).status_code == 422


def test_bars_endpoint_gives_closes(client) -> None:
    run_id = client.post("/api/runs", json={"scenario": "demo"}).json()["run_id"]
    deadline = time.time() + 30
    while time.time() < deadline:                          # 等运行结束（结束后 scope 才完整）
        if (Path("runs") / run_id / "journal.jsonl").read_text(encoding="utf-8").count("run.finished"):
            break
        time.sleep(0.2)
    body = client.get(f"/api/runs/{run_id}/bars").json()
    assert body["currency"] == "USD"
    assert len(body["bars"]) >= 200 and len(body["bars"]) <= 250
    assert {"date", "close"} <= set(body["bars"][0])
