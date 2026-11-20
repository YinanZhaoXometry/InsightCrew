"""check 命令：对配置的每个档位逐一探测模型。WP5 留空，WP6 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。

做法（BLUEPRINT 13 WP6）：先打印设置（api_key 是 SecretStr，JSON 形式输出时是掩码）；
对每个档位 build 出模型（失败原因与 wiring 相同，按 (RuntimeError, ValueError) 接住），
再对 auto、native、tool、prompted 各跑一次契约只有一个布尔值的探测角色——
走内核的 run_role，跑在临时运行上：内存 Journal（不落盘）、套在它上面的 Board、
没有上限的 Budget，全部直接构造。每个方式打印一行：回复自报的模型名、接受与否及原因、
token 用量、是否有价格。模型不支持某种方式是打印出来的结果，不是命令的失败；
一个档位都不可用（例如没有密钥）才以退出码 2 结束。

本文件不得引入 pydantic_ai：模型构造的失败按 (RuntimeError, ValueError) 接住即可。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..adapters.models import Tiers, build
from ..domain.types import Contract
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Journal
from ..kernel.runtime import Halt, Malformed, OutputMode, Role, Run, run_role
from .replay import _one_line  # 同一层的复用（20.7 第 14 条）：压缩异常文本为一行，只定义一次
from .settings import Settings

_MODES: tuple[OutputMode, ...] = ("auto", "native", "tool", "prompted")
_TIERS = ("quick", "deep")


class _Probe(Contract):
    """探测用的最小契约：一个布尔值。"""
    ok: bool


_ROLE = Role(name="check.probe", tier="quick", instructions="你是连通性探测。请按契约回答。",
             reads=("check/*",), writes="check/reply", output=_Probe)


def run(settings: Settings) -> int:
    """打印配置（不含密钥），并对每个档位在四种输出方式下各发一次小请求。"""
    print("设置（api_key 已掩码）：")
    print(json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2))

    if settings.force_offline:
        print("没有可用的模型：已设置 IC_FORCE_OFFLINE，忽略任何密钥，只运行离线演示")
        return 2

    models: dict[str, Any] = {}
    failed: list[str] = []
    for tier in _TIERS:
        name = getattr(settings.tiers, tier)
        try:
            models[tier] = build(name, base_url=settings.base_url,
                                 api_key=settings.api_key.get_secret_value()
                                 if settings.api_key else None)
        except (RuntimeError, ValueError) as e:          # 缺密钥、缺端点：库的 UserError 或 ValueError
            failed.append(f"档位 {tier}（{name}）没有可用模型：{_one_line(str(e))}")
    for line in failed:
        print(line)
    if not models:
        print("没有可用的模型：两个档位都无法构造。没有密钥时系统只运行离线演示"
              "（场景与回放，见 insightcrew scenarios / replay）。")
        return 2

    asyncio.run(_probe_all(models))
    return 0


async def _probe_all(models: dict[str, Any]) -> None:
    """逐档位逐方式探测并即时打印。全部探测在同一个事件循环里完成。"""
    for tier, model in models.items():
        print(f"档位 {tier}（配置名 {model.model_name}），四种输出方式：")
        for mode in _MODES:
            print(await _probe(tier, model, mode))


async def _probe(tier: str, model: Any, mode: OutputMode) -> str:
    """对 (档位, 输出方式) 跑一次探测角色，返回一行结果。"""
    journal = Journal()                                  # 内存日志：探测不留运行目录
    run = Run(id="check", journal=journal, board=Board(journal),
              models=Tiers(quick=model, deep=model), budget=Budget(), output_mode=mode)
    run.board.put("check/prompt", {"任务": "连通性探测：请按契约回答。"}, by="check")
    try:
        await run_role(run, _ROLE)
        verdict = "接受"
    except Malformed as e:                               # 纠正一次后仍不合契约
        verdict = f"未接受（输出不合契约：{_one_line(e.detail)}）"
    except Halt as e:                                    # 方式不被支持、请求失败、闸门拦下
        verdict = f"未接受（{e.reason}：{_one_line(e.detail)}）"

    called = next((e for e in journal.events if e.type == "model.called"), None)
    if called is None:                                   # 请求根本没发出去（例如方式被库拒绝）
        name, usage, cost = model.model_name, {"input_tokens": 0, "output_tokens": 0}, None
    else:                                                # 回复自报的模型名以 model.called 为准
        name, usage, cost = called.data["model"], called.data["usage"], called.data["cost"]
    tokens = f"{usage['input_tokens']}+{usage['output_tokens']} token"
    price = "已定价" if cost is not None else "未定价"
    return f"  {tier} · {mode}：模型 {name}，{verdict}，{tokens}，{price}"
