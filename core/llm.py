"""模型调用层：只用标准库（urllib）直连 OpenAI 兼容接口。

为什么不用官方 SDK：
    部署时少一个依赖，且"接口是什么样"这件事对答辩有用——
    你能解释 message / response_format / temperature 分别起什么作用。

两种模式：
    - 在线：IC_API_KEY 存在 -> 真的调模型
    - 离线：没有 key -> 走 ScriptedLLM 剧本，保证演示可复现、可离线跑
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
import urllib.error

from core.scripts import SCRIPTS


def _loads(text: str) -> dict:
    """从模型输出里抠出 JSON。模型经常裹 ```json 围栏或加解释文字。

    关键细节：只在**整段以围栏开头**时才剥围栏。
    否则像 {"code": "```python ...```"} 这种"JSON 内部含围栏"的正常回答
    会被误剥，导致解析失败——接真模型时这是最先炸的地方之一。
    """
    text = text.strip()
    if text.startswith("```"):
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i:j + 1]
    return json.loads(text)


class ScriptedLLM:
    """离线剧本：每个角色一个生成函数，按调用次数返回预置结果。

    这不是"作弊跑通"，而是把模型的不确定性从演示链路里摘出去，
    让你先验证编排、沙箱、审计这三件真正难的东西是否工作。
    """

    def __init__(self):
        self.counters: dict[str, int] = {}

    def take(self, agent: str, user: str = "") -> dict:
        i = self.counters.get(agent, 0)
        self.counters[agent] = i + 1
        fn = SCRIPTS.get(agent)
        return fn(i, user) if fn else {}


class LLM:
    def __init__(self, cfg, scripted: ScriptedLLM | None = None):
        self.cfg = cfg
        self.scripted = scripted
        self.tokens = 0
        self.calls = 0
        self.used_fallback = False     # 是否因接口不支持 response_format 而退化为纯文本解析

    def chat_json(self, agent: str, system: str, user: str) -> dict:
        self.calls += 1
        if self.cfg.offline:
            if self.scripted is None:
                raise RuntimeError("离线模式需要 ScriptedLLM")
            return self.scripted.take(agent, user)
        return self._post(system, user)

    def _post(self, system: str, user: str) -> dict:
        body = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        try:
            data = self._send(body)
        except urllib.error.HTTPError as e:
            # 有些兼容接口不支持 response_format（返回 400/422），去掉它重试一次。
            # 解析端本来就有"从文本里抠 JSON"的兜底，所以去掉也能工作。
            if e.code in (400, 404, 422):
                body.pop("response_format", None)
                self.used_fallback = True
                data = self._send(body)
            else:
                detail = e.read().decode("utf-8", "replace")[:400]
                raise RuntimeError(f"模型接口返回 {e.code}：{detail}") from e

        usage = data.get("usage") or {}
        self.tokens += int(usage.get("total_tokens", 0))
        try:
            return _loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise RuntimeError(f"模型输出无法解析为 JSON：{str(data)[:400]}") from e

    def _send(self, body: dict):
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.cfg.api_key}"},
        )
        # 网络抖动（读超时、连接被重置）在真实使用里是常态，不能一次就崩。
        # 退避重试；单次 120s、最多 3 次，最坏情况约 4 分钟，仍在时间预算内。
        # 仍失败才抛 RuntimeError，由编排器转成降级输出。
        last = None
        for wait in (0, 3, 8):
            if wait:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError:
                raise                      # 4xx/5xx 由调用方处理，不必重试
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
                last = e
                continue
        raise RuntimeError(f"模型接口连接失败（已重试 3 次）：{type(last).__name__}: {last}")
