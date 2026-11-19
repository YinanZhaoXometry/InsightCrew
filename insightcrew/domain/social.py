# Adapted from TradingAgents (Apache-2.0), modified.
"""社交情绪数据的契约与端口，以及情绪读数的审计检查（BLUEPRINT 19.7、19.12）。

六档情感带（Band 与 BAND_RANGES）取自 TradingAgents 的 SentimentBand 思想，
区间边界按 BLUEPRINT 19.7 所印照抄。帖子是个人观点，不是事件，也不是事实：
代码只核对帖子存在、它何时发布、信号引用的帖子 ID 是否存在、分数是否落在
自己声称的情感带里；读得对不对是判断，交给评审。
读取由 adapters 层实现（social_json、stocktwits、reddit）。
"""
from __future__ import annotations

import datetime
from typing import Literal, Protocol

from pydantic import Field

from .types import Contract, Finding


class Post(Contract):
    """一条社交帖子（端口层）。"""

    published: datetime.date
    source: str                    # "stocktwits" | "reddit" | "合成数据"
    text: str                      # 截断到 280 字
    url: str
    tag: Literal["bullish", "bearish", "none"]   # 来源自带的标签；没有时为 none


class SocialSet(Contract):
    """一次读取得到的窗口内帖子。"""

    source: str
    posts: tuple[Post, ...]
    dropped: int                   # 无日期而丢弃的条数


class Social(Protocol):
    """社交端口。给一个代码和窗口 [start, as_of]，返回窗口内（含两端）发布的帖子。

    抛 NotCovered（来源没有这个标的，或窗口超出其覆盖范围）、SourceUnavailable（读不出来）。
    """

    def posts(self, ticker: str, start: datetime.date, as_of: datetime.date) -> SocialSet: ...


class PostEntry(Contract):
    """板键 sentiment/posts 的元素。"""

    id: str                        # "S1"……
    published: str
    source: str
    text: str
    tag: str


class SocialCard(Contract):
    """板键 sentiment/card：与 NewsCard 同形。"""

    source: str
    window_from: str               # 窗口起点（ISO）
    window_to: str                 # 窗口终点（ISO），即分析日
    items: int                     # 窗口内的帖子数（截断后）
    dropped: int                   # 无日期而被丢弃的帖子数
    truncated: int                 # 超出上限而被截掉的帖子数


Band = Literal["bullish", "mildly_bullish", "neutral", "mixed", "mildly_bearish", "bearish"]


class SentimentSignal(Contract):
    """情绪分析师对一条帖子的读数。方向是判断，不由代码核对。"""

    post: str = Field(description="被解读的帖子 ID（S 加数字）")
    direction: Literal["positive", "negative", "neutral"]
    reading: str


class SentimentAnalysis(Contract):
    """情绪分析师的完整读数（板键 sentiment/analysis）。"""

    band: Band
    score: float = Field(ge=0, le=10, description="0 最看空，10 最看多")
    confidence: Literal["low", "medium", "high"]
    signals: tuple[SentimentSignal, ...]
    narrative: str


BAND_RANGES: dict[str, tuple[float, float]] = {
    "bearish": (0, 2), "mildly_bearish": (2, 4), "neutral": (4, 6),
    "mixed": (4, 6), "mildly_bullish": (6, 8), "bullish": (8, 10)}


def _fmt(x: float) -> str:
    """数字进中文消息之前去掉多余的尾零。"""
    return f"{x:.6g}"


def sentiment_findings(entries: list[PostEntry], analysis: SentimentAnalysis) -> list[Finding]:
    """情绪通道的三项检查（BLUEPRINT 19.12），owner 一律 "sentiment"：

    1. sentiment_has_signals：窗口里有帖子却没有给出任何信号；
    2. sentiment_signal_cites_known_post：信号引用的帖子 ID 不在帖子列表里；
    3. sentiment_score_matches_band：分数落在自己声称的情感带的区间之外
       （区间两端含，BAND_RANGES；6 分既算 neutral 也算 mixed，4 分同理）。
    """
    known = {e.id for e in entries}
    out: list[Finding] = []
    if entries and not analysis.signals:
        out.append(Finding(check="sentiment_has_signals", layer="L2", owner="sentiment",
                           subject="sentiment.analysis",
                           message="窗口内有帖子，情绪分析却没有给出任何信号"))
    for i, signal in enumerate(analysis.signals):
        if signal.post not in known:
            out.append(Finding(check="sentiment_signal_cites_known_post", layer="L2",
                               owner="sentiment", subject=f"sentiment.signals[{i}]",
                               message=f"信号引用的帖子 {signal.post} 不在帖子列表中"))
    low, high = BAND_RANGES[analysis.band]
    if not low <= analysis.score <= high:
        out.append(Finding(check="sentiment_score_matches_band", layer="L2", owner="sentiment",
                           subject="sentiment.score", limit=low if analysis.score < low else high,
                           actual=analysis.score,
                           message=f"情绪分 {_fmt(analysis.score)} 不在情感带 {analysis.band} 的 "
                                   f"区间 [{_fmt(low)}, {_fmt(high)}] 之内"))
    return out
