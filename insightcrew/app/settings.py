"""设置：insightcrew.toml、环境变量与 .env 的唯一读取处。

优先级：命令行旗标 > 进程环境 > .env（.env 只补充环境里没有的变量）> insightcrew.toml > 默认值。
环境变量的形状：IC_<分节>__<字段> 覆盖文件里的一个键（如 IC_FLOW__JUDGES）；
IC_BASE_URL、IC_API_KEY、IC_SEC_CONTACT、IC_FORCE_OFFLINE、IC_TODAY 是根级变量。
未知键一律报错并点名：写错的配置要在启动时失败，而不是在运行中被悄悄忽略。
密钥（IC_API_KEY、IC_SEC_CONTACT）是 SecretStr，打印设置时不会出现。
"""
from __future__ import annotations

import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..flows.analyze import FlowConfig

_PREFIX = "IC_"
_DELIM = "__"
_SECTIONS = ("run", "flow", "limits", "budget", "tiers", "disclosures")
_ROOT_VARS = ("base_url", "api_key", "sec_contact", "force_offline", "today")    # 不带分节的根级变量


class _Section(BaseModel):
    """配置文件里的一个分节：封闭。写错键名在启动时报错，而不是被悄悄忽略。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RunSection(_Section):
    universe: str
    output_mode: Literal["auto", "native", "tool", "prompted"]


class LimitsSection(_Section):
    max_requests: int
    max_seconds: float
    request_timeout_s: float


class BudgetSection(_Section):
    run_tokens: int
    run_cost: float
    batch_tokens: int
    batch_cost: float
    roles: dict[str, int] = {}    # [budget.roles]：每个角色组的 token 上限，可选


class TiersSection(_Section):
    quick: str
    deep: str


class DisclosuresSection(_Section):
    """公告通道的两个限制（BLUEPRINT 18.8）。带默认值：配置文件不写这一节也能跑。"""

    window_days: int = 90                # 窗口长度：[as_of − window_days, as_of]
    max_items: int = 30                  # 交给角色的条目上限，超过按新到旧截断


class Settings(BaseSettings):
    """整个应用的一份只读配置。分节来自文件与环境，根级变量只来自环境。"""

    model_config = SettingsConfigDict(env_prefix=_PREFIX, env_nested_delimiter=_DELIM,
                                      extra="forbid", frozen=True)

    run: RunSection
    flow: FlowConfig
    limits: LimitsSection
    budget: BudgetSection
    tiers: TiersSection
    disclosures: DisclosuresSection = DisclosuresSection()
    base_url: str | None = None          # 裸模型名用的 OpenAI 兼容端点
    api_key: SecretStr | None = None     # 裸模型名用的密钥；打印时显示为掩码
    sec_contact: SecretStr | None = None     # IC_SEC_CONTACT：下载美股数据时自报的联系方式；打印时同样掩码
    force_offline: bool = False          # IC_FORCE_OFFLINE：忽略任何密钥，只运行离线演示
    today: date | None = None            # IC_TODAY：把"今天"钉住，离线演示在任何一天结果相同


def load(root: Path, **flags: Any) -> Settings:
    """读入设置。root 是仓库根；flags 是命令行旗标的覆盖，键为分节名或根级字段。

    分节用字典覆盖（可以只覆盖其中几个键），根级字段直接给值：
    load(root, run={"universe": "real"}, flow={"judges": False}, force_offline=True)
    """
    load_dotenv(root / ".env", override=False)    # 只补充环境里没有的变量；供应商密钥留给库去找
    file = tomllib.loads((root / "insightcrew.toml").read_text(encoding="utf-8"))
    merged = _merge(_merge(file, _env_overrides()), flags)
    return Settings(**merged)


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """深合并：overlay 逐键覆盖 base。分节内部是按键合并，不是整节替换，
    所以 IC_FLOW__JUDGES 只改这一个键，分节里其余键仍取文件里的值。"""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _env_overrides() -> dict[str, Any]:
    """收集 IC_* 环境变量。写错的变量名在启动时报错并点名，而不是被悄悄忽略。"""
    out: dict[str, Any] = {}
    sections: dict[str, dict[str, Any]] = {}
    for name, value in os.environ.items():
        if not name.startswith(_PREFIX) or name == _PREFIX:
            continue
        key = name[len(_PREFIX):]
        if _DELIM in key:
            section, field = key.split(_DELIM, 1)
            if section.lower() not in _SECTIONS:
                raise ValueError(f"未知的环境变量 {name}：{_PREFIX}<分节>__<字段> 里的分节 {section!r} 不存在")
            sections.setdefault(section.lower(), {})[field.lower()] = value
        elif key.lower() in _ROOT_VARS:
            out[key.lower()] = value
        else:
            raise ValueError(f"未知的环境变量 {name}")
    out.update(sections)
    return out
