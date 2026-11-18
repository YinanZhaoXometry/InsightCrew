"""真实模型路径的测试。不联网：用 httpx2 的 MockTransport 扮演一个 OpenAI 兼容的服务端。"""
import json

import httpx2
import pytest
from pydantic_ai import UserError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from insightcrew.adapters.models import Tiers, build
from insightcrew.domain.types import Verdict
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Halt, Role, Run, run_role

KEY = "sk-secret-do-not-leak"
VERDICT = {"accept": True, "target": "none", "critique": "", "cites": []}
JUDGE = Role("judge.evidence", "quick", "你是评审。", ("x/*",), "judge.evidence/verdict", Verdict, effort="high")


class Server:
    """记录收到的请求；按 failures 里的状态码先失败若干次，然后正常回答。"""

    def __init__(self, failures: tuple[int, ...] = ()):
        self.bodies: list[dict] = []
        self.auth: list[str] = []
        self._failures = list(failures)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.auth.append(request.headers["authorization"])
        if self._failures:
            return httpx2.Response(self._failures.pop(0), json={"error": {"message": "boom"}})
        message: dict = {"role": "assistant", "content": None}
        if body.get("tools"):                              # 模型被要求用工具调用返回结构化结果
            call = {"name": body["tools"][0]["function"]["name"], "arguments": json.dumps(VERDICT)}
            message["tool_calls"] = [{"id": "c1", "type": "function", "function": call}]
        else:
            message["content"] = json.dumps(VERDICT)
        return httpx2.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290}})


def deepseek(server: Server) -> Model:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return OpenAIChatModel("deepseek-chat", provider=DeepSeekProvider(api_key=KEY, http_client=client))


def compatible(server: Server) -> Model:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(server))
    return build("some-model", base_url="https://llm.example/v1", api_key=KEY, http_client=client)


def make_run(model: Model, **kw) -> Run:
    journal = Journal()
    run = Run("r", journal, Board(journal), Tiers(quick=model, deep=model), Budget(), **kw)
    run.board.put("x/fact", {"id": "close", "value": 100.0}, by="ledger")
    return run


async def test_a_real_call_is_recorded_priced_and_keeps_the_key_out():
    server = Server()
    run = make_run(deepseek(server))
    assert (await run_role(run, JUDGE)).accept is True
    event = next(e for e in run.journal.events if e.type == "model.called")
    assert event.data["model"] == "deepseek-chat"
    assert event.data["usage"] == {"requests": 1, "input_tokens": 1234, "output_tokens": 56}
    assert event.data["cost"] > 0                        # 价格来自库自带的价格表
    assert server.auth == [f"Bearer {KEY}"]
    assert KEY not in "".join(e.model_dump_json() for e in run.journal.events)
    sent = json.dumps(server.bodies[0], ensure_ascii=False)
    assert "你是评审。" in sent and "ledger" not in sent and "x/fact" in sent
    assert not {"reasoning_effort", "thinking"} & set(server.bodies[0])    # 该模型不支持调节推理力度：库不发送


async def test_a_model_without_a_known_price_is_unpriced():
    run = make_run(compatible(Server()))
    await run_role(run, JUDGE)
    assert next(e for e in run.journal.events if e.type == "model.called").data["cost"] is None
    assert run.budget.table()[0]["cost"] is None and run.budget.cost == 0.0


@pytest.mark.parametrize(("make", "mode", "fmt"), [
    (deepseek, "auto", None),                 # 该模型的能力档案选了工具调用
    (deepseek, "prompted", "json_object"),
    (compatible, "native", "json_schema"),
    (compatible, "prompted", "json_object"),
])
async def test_output_mode_follows_the_model_profile_or_the_setting(make, mode, fmt):
    server = Server()
    run = make_run(make(server), output_mode=mode)
    await run_role(run, JUDGE)
    body = server.bodies[0]
    assert (body.get("response_format") or {}).get("type") == fmt
    assert bool(body.get("tools")) is (fmt is None)


async def test_a_mode_the_model_cannot_do_stops_the_run_without_a_request():
    server = Server()
    run = make_run(deepseek(server), output_mode="native")
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(run, JUDGE)
    assert server.bodies == []


async def test_transient_errors_are_retried_and_auth_errors_are_not():
    server = Server(failures=(500,))
    assert (await run_role(make_run(deepseek(server)), JUDGE)).accept is True
    assert len(server.bodies) == 2

    server = Server(failures=(401,))
    run = make_run(deepseek(server))
    with pytest.raises(Halt, match="model_unavailable"):
        await run_role(run, JUDGE)
    assert len(server.bodies) == 1
    failed = run.journal.events[-1]
    assert failed.type == "model.failed" and KEY not in failed.model_dump_json()


def test_tiers_route_by_role_and_names_resolve(monkeypatch):
    quick, deep = compatible(Server()), deepseek(Server())
    tiers = Tiers(quick=quick, deep=deep)
    assert tiers.for_role(JUDGE) is quick
    assert tiers.names() == {"quick": "some-model", "deep": "deepseek-chat"}

    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    assert build("deepseek:deepseek-chat").system == "deepseek"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(UserError):                       # 缺密钥：由装配层决定退回离线演示
        build("deepseek:deepseek-chat")
    with pytest.raises(ValueError):
        build("bare-name")
