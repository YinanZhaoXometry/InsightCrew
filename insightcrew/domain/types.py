"""领域契约。角色之间传递的每一种结构都在这里定义，且只在这里定义。

全部是不可变的 Pydantic 模型，不接受契约之外的字段。数值与等级只出现在这些结构里，绝不藏在自由文本中。
字段的 description 会进入发给模型的输出格式说明，所以它既是文档，也是提示词的一部分。
"""
from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Rating(enum.StrEnum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class Action(enum.StrEnum):
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class Fact(Contract):
    """由代码算出的一个数。模型永远不产生 Fact。"""

    id: str                       # 语义化 ID，例如 "close"、"rsi_14"、"atr_14"
    label: str                    # 中文名，用于报告
    value: FiniteFloat
    unit: str                     # "USD" | "HKD" | "%" | "" | "shares"
    as_of: str                    # 这个数所属的交易日（ISO），不晚于分析日
    source: str                   # 计算它的函数，例如 "indicators.rsi"
    bars_used: int


class Claim(Contract):
    fact: str = Field(description="被引用的事实 ID，必须出现在给你的事实列表中")
    value: FiniteFloat = Field(description="该事实的数值，照抄，不要四舍五入到别的精度")
    reading: str = Field(description="你对这个数的解读，一句话，不要包含其他数字")


class Analysis(Contract):
    claims: tuple[Claim, ...]
    narrative: str = Field(description="整体判断，三到五句")


class Point(Contract):
    text: str
    cites: tuple[str, ...] = Field(description="支撑这一点的事实 ID，至少一个")


class Argument(Contract):
    points: tuple[Point, ...]
    rebuttal: str = Field(description="对对方上一轮论点的回应；对方尚未发言时写空字符串")


class Thesis(Contract):
    rating: Rating
    horizon_days: int = Field(description="预期持有的交易日数")
    points: tuple[Point, ...]
    invalidation: tuple[str, ...] = Field(description="哪些情况出现就说明这个判断错了")
    summary: str


class Proposal(Contract):
    action: Action
    entry: FiniteFloat | None = Field(description="入场价，绝对价格。action 不是 Buy 时为 null")
    stop: FiniteFloat | None = Field(description="止损价，绝对价格。action 不是 Buy 时为 null")
    target: FiniteFloat | None = Field(description="目标价，绝对价格，可为 null")
    size_pct: FiniteFloat = Field(description="仓位占权益的百分比，0 到 100")
    horizon_days: int
    cites: tuple[str, ...]
    rationale: str


class Verdict(Contract):
    accept: bool
    target: Literal["thesis", "levels", "none"] = Field(
        description="不接受时，问题出在论点（thesis）还是价位与仓位（levels）；接受时为 none")
    critique: str
    cites: tuple[str, ...]


class Finding(Contract):
    """审计发现的一个问题。owner 是能修它的角色。"""

    check: str                    # 检查项或规则的键
    layer: Literal["L2", "L3"]
    owner: Literal["technical", "manager", "trader"]
    subject: str                  # 出问题的对象，例如 "claims[2]"、"proposal.stop"
    message: str
    limit: float | None = None
    actual: float | None = None


class Outcome(Contract):
    status: Literal["delivered", "review"]
    reason: str                   # delivered 时为 "ok"；review 时为停止原因
    facts_and_rules: Literal["verified", "unverified"]
    judgment: str                 # "accepted 2/3" | "contested" | "not reviewed: <原因>"


class RuleSpec(Contract):
    """规则手册里一条规则的声明。由 adapters 从 data/rules.toml 读入，不进入看板。"""

    key: str                      # 与 domain/rules.py 里的同名评估函数对应
    limit: float | None = None    # 数值型上限/下限；结构规则没有 limit，TOML 里省略该键
    formula: str                  # 给交易员和报告看的公式文字，不会被求值
    desc: str
    source: str                   # 这条规则出自风控手册的哪一条


class RuleResult(Contract):
    """一条规则的一次评估。status 为 n/a 表示规则不适用于这个提案，不是通过。"""

    key: str
    status: Literal["pass", "fail", "n/a"]
    limit: float | None = None    # 本次比较实际使用的界限（cash_sufficient 用调用方给的现金占比）
    actual: float | None = None   # 规则算出的实际值；不适用或无从计算时为 null
    message: str                  # 一句中文，说明结果或为什么不适用


class Unavailable(Contract):
    """一条算不出来的事实：记下它的 ID 与原因，而不是悄悄少一个数。"""

    id: str                       # 事实 ID，与 indicators.toml 的 outputs 一致
    reason: str                   # 一句中文，说明为什么算不出来


class DataCard(Contract):
    """数据卡：一次分析所用行情数据的画像，随事实一起上黑板（ledger/card）。"""

    first_bar: str                # 第一根已结算 K 线的日期（ISO）
    last_bar: str                 # 最后一根已结算 K 线的日期（ISO）
    bars: int                     # 交给账本的已结算 K 线根数
    stale_days: int               # 最后一根 K 线到分析日的自然日数
    dropped_rows: int             # 数据源里 close 为空、被丢弃的行数
    gaps: tuple[str, ...]         # 超过五个自然日没有 K 线的时段，一句中文一段
    unavailable: tuple[Unavailable, ...]   # 历史不足而算不出的事实
    warnings: tuple[str, ...]     # 数据异常提醒，例如单日涨跌超过 40% 的疑似除权/除息


class Scope(Contract):
    """准入的结果：这次分析对象是谁、到哪一天为止。之后任何阶段只从这里拿范围。"""

    ticker: str                   # 规范化后的代码
    as_of: str                    # 分析日（ISO）；不晚于今天
    universe: str                 # 数据集名字
    requested: str                # 用户原文里敲的代码，未规范化


class AuditStatus(Contract):
    """审计的结论与它通过时的三个版本号：方案、论点、读数任何一个变了，审计就算过期。"""

    verdict: Literal["pass", "revise"]
    proposal_seq: int             # 通过审计时的 trader/proposal 事件序号
    thesis_seq: int               # 通过审计时的 manager/thesis 事件序号
    analysis_seq: int             # 通过审计时的 technical/analysis 事件序号
