"""真实模型。协议、重试、结构化输出方式、各家能力差异和价格，全部由 Pydantic AI 负责；
这里只做两件事：把配置里的名字变成模型，把角色的档位映射到模型。

换模型 = 改配置里的一个名字。这个文件不需要动。
"""
from __future__ import annotations

from typing import Any

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from ..kernel.runtime import Role


def build(name: str, *, base_url: str | None = None, api_key: str | None = None,
          http_client: Any = None) -> Model:
    """name 有两种写法：

    "供应商:模型名"（例如 "deepseek:deepseek-flash"、"openai:..."、"anthropic:..."）
        由 Pydantic AI 识别供应商，密钥取该供应商约定的环境变量（例如 DEEPSEEK_API_KEY）。
        缺密钥或缺该供应商的依赖包时抛 pydantic_ai.UserError / ImportError。
    裸模型名（例如 "my-model"）
        任意 OpenAI 兼容端点，必须同时给出 base_url 和 api_key。
    """
    if ":" in name:
        return infer_model(name)
    if not base_url or not api_key:
        raise ValueError(f"模型 {name!r} 没有供应商前缀，需要同时设置 base_url 和 api_key")
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=base_url, api_key=api_key,
                                                         http_client=http_client))


class Tiers:
    """按角色声明的档位取模型：quick 给分析、辩论、评审，deep 给需要综合判断的角色。"""

    def __init__(self, quick: Model, deep: Model):
        self._by_tier = {"quick": quick, "deep": deep}

    def for_role(self, role: Role) -> Model:
        return self._by_tier[role.tier]

    def names(self) -> dict[str, str]:
        return {tier: model.model_name for tier, model in self._by_tier.items()}
