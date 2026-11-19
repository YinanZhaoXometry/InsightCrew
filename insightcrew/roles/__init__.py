"""角色声明。一个角色 = 职责（提示词）+ 能看到什么 + 写到哪里 + 输出契约 + 模型档位。

想知道某个角色看不到什么，读它的 reads 就够了：没列出的键，它一概看不到。
提示词放在 prompts/*.md，只描述职责和约束，不含数据，也不含输出格式（格式由契约生成）。
"""
from __future__ import annotations

from pathlib import Path

from ..domain.types import Analysis, Argument, Proposal, Thesis, Verdict
from ..kernel.runtime import Role

_DIR = Path(__file__).parent / "prompts"
# 输出上限含推理 token：deepseek-flash 与 deepseek-v4-pro 默认先推理，内核默认的 2000 会把回答截掉。
_QUICK_OUT = 8000
_DEEP_OUT = 12000


def _prompt(name: str, **fill: str) -> str:
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in fill.items():
        text = text.replace("{" + key + "}", value)
    return text.rstrip() + "\n\n" + (_DIR / "_common.md").read_text(encoding="utf-8").rstrip()


TECHNICAL = Role(
    "technical", "quick", _prompt("technical"),
    reads=("ledger/instrument", "ledger/facts", "ledger/card", "audit/technical"),
    writes="technical/analysis", output=Analysis, max_output_tokens=_QUICK_OUT)

BULL = Role(
    "bull", "quick", _prompt("bull"),
    reads=("ledger/instrument", "technical/analysis", "bear/argument"),
    writes="bull/argument", output=Argument, max_output_tokens=_QUICK_OUT)

BEAR = Role(
    "bear", "quick", _prompt("bear"),
    reads=("ledger/instrument", "technical/analysis", "bull/argument"),
    writes="bear/argument", output=Argument, max_output_tokens=_QUICK_OUT)

MANAGER = Role(
    "manager", "deep", _prompt("manager"),
    reads=("ledger/instrument", "ledger/card", "technical/analysis", "bull/argument", "bear/argument",
           "memory/lessons", "audit/manager", "judges/on_thesis"),
    writes="manager/thesis", output=Thesis, effort="high",
    max_output_tokens=_DEEP_OUT)

TRADER = Role(
    "trader", "quick", _prompt("trader"),
    reads=("ledger/instrument", "ledger/facts", "manager/thesis", "portfolio/state",
           "rules/limits", "audit/trader", "judges/on_levels"),
    writes="trader/proposal", output=Proposal, max_output_tokens=_QUICK_OUT)

LENSES = {
    "evidence": "证据。每个论点是否真的由它引用的事实推出？有没有被忽略的、指向相反方向的事实？",
    "conservative": "下行风险。正常波动是否就会触发止损？失效条件是否具体？判断错了的话，损失是否如方案所述？",
    "aggressive": "机会成本。相对于论点的强度，方案是否过于保守？例如评级明确却不行动，或仓位小到没有意义。",
}

JUDGES = tuple(
    Role(f"judge.{lens}", "quick", _prompt("judge", lens=brief),
         reads=("ledger/instrument", "ledger/facts", "technical/analysis", "manager/thesis",
                "trader/proposal", "rules/limits", "audit/rules"),
         writes=f"judge.{lens}/verdict", output=Verdict, effort="low", max_output_tokens=_QUICK_OUT)
    for lens, brief in LENSES.items())

ALL = (TECHNICAL, BULL, BEAR, MANAGER, TRADER, *JUDGES)
