"""角色声明。一个角色 = 职责（提示词）+ 能看到什么 + 写到哪里 + 输出契约 + 模型档位。

想知道某个角色看不到什么，读它的 reads 就够了：没列出的键，它一概看不到。
提示词放在 prompts/*.md，只描述职责和约束，不含数据，也不含输出格式（格式由契约生成）。
"""
from __future__ import annotations

from pathlib import Path

from ..domain.memory import Lesson
from ..domain.news import NewsAnalysis
from ..domain.risk import RiskView
from ..domain.social import SentimentAnalysis
from ..domain.types import Analysis, Argument, DisclosureAnalysis, Proposal, Thesis, Verdict
from ..kernel.runtime import Role

_DIR = Path(__file__).parent / "prompts"
# 输出上限含推理 token：deepseek-flash 与 deepseek-v4-pro 默认先推理，内核默认的 2000 会把回答截掉。
_QUICK_OUT = 8000
_DEEP_OUT = 12000


def _prompt(name: str, **fill: str) -> str:
    """读一份提示词。以 <!-- 开头的行是版权署名（BLUEPRINT 19.4），模型永远看不到。"""
    lines = (_DIR / f"{name}.md").read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.startswith("<!--"))
    for key, value in fill.items():
        text = text.replace("{" + key + "}", value)
    return text.rstrip() + "\n\n" + (_DIR / "_common.md").read_text(encoding="utf-8").rstrip()


TECHNICAL = Role(
    "technical", "quick", _prompt("technical"),
    reads=("ledger/instrument", "ledger/facts", "ledger/card", "audit/technical"),
    writes="technical/analysis", output=Analysis, max_output_tokens=_QUICK_OUT)

# 证据通道的键（BLUEPRINT 18.9、19.12）：插在 technical/analysis 之后。通道关闭时这些键不上黑板，
# Board.view 跳过缺席的键，所以不开通道的运行提示词与版本 2.0 逐字相同。
_CHANNELS_BRIEF = ("fundamentals/status", "fundamentals/analysis",
                   "disclosures/status", "disclosures/analysis",
                   "news/status", "news/analysis",
                   "sentiment/status", "sentiment/analysis")

NEWS = Role(
    "news", "quick", _prompt("news"),
    reads=("ledger/instrument", "news/items", "news/card", "news/status",
           "macro/facts", "macro/markets", "macro/status", "audit/news"),
    writes="news/analysis", output=NewsAnalysis, max_output_tokens=_QUICK_OUT)

SENTIMENT = Role(
    "sentiment", "quick", _prompt("sentiment"),
    reads=("ledger/instrument", "sentiment/posts", "sentiment/card",
           "sentiment/status", "audit/sentiment"),
    writes="sentiment/analysis", output=SentimentAnalysis, max_output_tokens=_QUICK_OUT)

BULL = Role(
    "bull", "quick", _prompt("bull"),
    reads=("ledger/instrument", "technical/analysis", *_CHANNELS_BRIEF, "bear/argument"),
    writes="bull/argument", output=Argument, max_output_tokens=_QUICK_OUT)

BEAR = Role(
    "bear", "quick", _prompt("bear"),
    reads=("ledger/instrument", "technical/analysis", *_CHANNELS_BRIEF, "bull/argument"),
    writes="bear/argument", output=Argument, max_output_tokens=_QUICK_OUT)

MANAGER = Role(
    "manager", "deep", _prompt("manager"),
    reads=("ledger/instrument", "ledger/card", "technical/analysis", *_CHANNELS_BRIEF,
           "bull/argument", "bear/argument",
           "memory/lessons", "audit/manager", "judges/on_thesis"),
    writes="manager/thesis", output=Thesis, effort="high",
    max_output_tokens=_DEEP_OUT)

_RISK_KEYS = ("risk/aggressive", "risk/neutral", "risk/conservative")

RISK_AGGRESSIVE = Role(
    "risk.aggressive", "quick", _prompt("risk_aggressive"),
    reads=("ledger/instrument", "ledger/facts", "manager/thesis", "trader/proposal",
           "portfolio/state", "rules/limits", *_RISK_KEYS),
    writes="risk/aggressive", output=RiskView, max_output_tokens=_QUICK_OUT)

RISK_NEUTRAL = Role(
    "risk.neutral", "quick", _prompt("risk_neutral"),
    reads=("ledger/instrument", "ledger/facts", "manager/thesis", "trader/proposal",
           "portfolio/state", "rules/limits", *_RISK_KEYS),
    writes="risk/neutral", output=RiskView, max_output_tokens=_QUICK_OUT)

RISK_CONSERVATIVE = Role(
    "risk.conservative", "quick", _prompt("risk_conservative"),
    reads=("ledger/instrument", "ledger/facts", "manager/thesis", "trader/proposal",
           "portfolio/state", "rules/limits", *_RISK_KEYS),
    writes="risk/conservative", output=RiskView, max_output_tokens=_QUICK_OUT)

TRADER = Role(
    "trader", "quick", _prompt("trader"),
    reads=("ledger/instrument", "ledger/facts", "manager/thesis", "portfolio/state",
           "rules/limits", "audit/trader", "judges/on_levels", *_RISK_KEYS),
    writes="trader/proposal", output=Proposal, max_output_tokens=_QUICK_OUT)

REFLECTOR = Role(
    "reflector", "quick", _prompt("reflector"),
    reads=("settle/decision", "settle/outcome"),
    writes="settle/lesson", output=Lesson, max_output_tokens=_QUICK_OUT)

# 技术分析师的两种声明（19.11）：不开工具时与 2.1 相同；开工具时多一个 indicator 工具。
# max_tool_calls 的实际值来自 [tools] 设置，装配处（wiring）用 dataclasses.replace 换入。
TECHNICAL_TOOLS = Role(
    "technical", "quick", _prompt("technical"),
    reads=TECHNICAL.reads, writes=TECHNICAL.writes, output=Analysis,
    tools=("indicator",), max_tool_calls=6, max_output_tokens=_QUICK_OUT)

# 两个证据通道的分析师（BLUEPRINT 18.9）。只在通道打开且状态为 available 时被调用；
# 各读各的证据，看不到价格事实、看不到彼此的输出。
FUNDAMENTALS = Role(
    "fundamentals", "quick", _prompt("fundamentals"),
    reads=("ledger/instrument", "fundamentals/facts", "fundamentals/card", "audit/fundamentals"),
    writes="fundamentals/analysis", output=Analysis, max_output_tokens=_QUICK_OUT)

DISCLOSURES = Role(
    "disclosures", "quick", _prompt("disclosures"),
    reads=("ledger/instrument", "disclosures/items", "disclosures/card", "audit/disclosures"),
    writes="disclosures/analysis", output=DisclosureAnalysis, max_output_tokens=_QUICK_OUT)

LENSES = {
    "evidence": "证据。每个论点是否真的由它引用的事实推出？有没有被忽略的、指向相反方向的事实？",
    "conservative": "下行风险。正常波动是否就会触发止损？失效条件是否具体？判断错了的话，损失是否如方案所述？",
    "aggressive": "机会成本。相对于论点的强度，方案是否过于保守？例如评级明确却不行动，或仓位小到没有意义。",
}

JUDGES = tuple(
    Role(f"judge.{lens}", "quick", _prompt("judge", lens=brief),
         reads=("ledger/instrument", "ledger/facts", "technical/analysis", *_CHANNELS_BRIEF,
                "fundamentals/facts", "disclosures/items", "manager/thesis",
                "trader/proposal", "rules/limits", "audit/rules"),
         writes=f"judge.{lens}/verdict", output=Verdict, effort="low", max_output_tokens=_QUICK_OUT)
    for lens, brief in LENSES.items())

# TECHNICAL_TOOLS 是 TECHNICAL 的开工具形态（同一个角色、同一个键），不单独计入 ALL。
ALL = (TECHNICAL, FUNDAMENTALS, DISCLOSURES, NEWS, SENTIMENT,
       BULL, BEAR, RISK_AGGRESSIVE, RISK_NEUTRAL, RISK_CONSERVATIVE,
       MANAGER, TRADER, REFLECTOR, *JUDGES)
