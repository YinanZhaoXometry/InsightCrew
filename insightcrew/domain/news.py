"""新闻与预测市场数据的契约与端口，以及新闻读数的审计检查（BLUEPRINT 19.7、19.12）。

新闻端口（port shape）照抄 domain/disclosures.py 的样子：条目是文本，不是数字。
代码只核对条目存在、它何时发布、信号引用的条目 ID 是否存在、claim 是否引用了
存在的宏观事实并照抄了它的数值；标题与预测市场行情是来源原文，照录不核，
读得对不对是判断，交给评审。读取由 adapters 层实现（news_json、news_rss）。
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from .types import Claim, Contract, Fact, Finding

if TYPE_CHECKING:
    # 只作类型标注用（19.7 把 markets 参数印成 "MarketEntry" 前向引用）；运行时不导入。
    from .macro import MarketEntry


class Headline(Contract):
    """一条已发布的新闻标题（端口层）。"""

    published: datetime.date
    source: str
    title: str
    url: str


class NewsSet(Contract):
    """一次读取得到的窗口内条目。"""

    source: str
    items: tuple[Headline, ...]
    dropped: int                           # 无日期而丢弃的条数


class News(Protocol):
    """新闻端口。给一个代码和窗口 [start, as_of]，返回窗口内（含两端）发布的标题。

    抛 NotCovered（来源没有这个标的，或窗口超出其覆盖范围）、SourceUnavailable（读不出来）。
    """

    def headlines(self, ticker: str, start: datetime.date, as_of: datetime.date) -> NewsSet: ...


class NewsEntry(Contract):
    """板键 news/items 的元素。"""

    id: str                                # "N1"……
    published: str
    source: str
    title: str
    url: str


class NewsCard(Contract):
    """板键 news/card。"""

    source: str
    window_from: str                       # 窗口起点（ISO）
    window_to: str                         # 窗口终点（ISO），即分析日
    items: int                             # 窗口内的条目数（截断后）
    dropped: int                           # 无日期而被丢弃的条目数
    truncated: int                         # 超出上限而被截掉的条目数


class NewsSignal(Contract):
    """新闻分析师对一条新闻或一个预测市场行情的读数。方向是判断，不由代码核对。"""

    item: str = Field(description="被解读的新闻（N 加数字）或预测市场（P 加数字）条目 ID")
    direction: Literal["positive", "negative", "neutral"]
    reading: str


class NewsAnalysis(Contract):
    """新闻分析师的完整读数（板键 news/analysis）。"""

    signals: tuple[NewsSignal, ...]
    claims: tuple[Claim, ...] = Field(description="对宏观事实的引用；没有宏观事实时为空")
    stance: Literal["positive", "mixed", "neutral", "negative"]
    confidence: Literal["low", "medium", "high"]
    narrative: str


# claim 值与事实值的容差，与 domain/audit.py 的技术面、基本面同一套。
_TOL_PCT = 0.005
_TOL_MIN = 0.01


def _fmt(x: float) -> str:
    """数字进中文消息之前去掉多余的尾零。"""
    return f"{x:.6g}"


def news_findings(entries: list[NewsEntry], markets: list[MarketEntry], macro: list[Fact],
                  analysis: NewsAnalysis) -> list[Finding]:
    """新闻通道的审计检查（BLUEPRINT 19.12），owner 一律 "news"：

    1. news_has_signals：窗口里有新闻条目或预测市场行情，却没有给出任何信号；
    2. news_signal_cites_known_item：信号引用的条目 ID（N 或 P 加数字）既不在
       新闻列表里，也不在预测市场列表里；
    3. claim_cites_known_fact / claim_value_matches_fact：claim 引用的宏观事实
       不存在，或数值与事实不符（与 audit.py 的技术面、基本面同一套容差；
       news 的 claims 没有最少条数要求，宏观通道关闭时允许为空）。
    """
    known_items = {e.id for e in entries} | {m.id for m in markets}
    known_facts = {f.id: f for f in macro}
    out: list[Finding] = []
    if (entries or markets) and not analysis.signals:
        out.append(Finding(check="news_has_signals", layer="L2", owner="news",
                           subject="news.analysis",
                           message="窗口内有新闻或预测市场行情，新闻分析却没有给出任何信号"))
    for i, signal in enumerate(analysis.signals):
        if signal.item not in known_items:
            out.append(Finding(check="news_signal_cites_known_item", layer="L2", owner="news",
                               subject=f"news.signals[{i}]",
                               message=f"信号引用的条目 {signal.item} 不在新闻或预测市场列表中"))
    for i, claim in enumerate(analysis.claims):
        fact = known_facts.get(claim.fact)
        if fact is None:
            out.append(Finding(check="claim_cites_known_fact", layer="L2", owner="news",
                               subject=f"news.claims[{i}]",
                               message=f"claim 引用的事实 {claim.fact} 不在宏观事实中"))
            continue
        tolerance = max(_TOL_PCT * abs(fact.value), _TOL_MIN)
        if abs(claim.value - fact.value) > tolerance:
            out.append(Finding(check="claim_value_matches_fact", layer="L2", owner="news",
                               subject=f"news.claims[{i}]", limit=fact.value, actual=claim.value,
                               message=f"claim 声称 {claim.fact} 为 {_fmt(claim.value)}，"
                                       f"与事实值 {_fmt(fact.value)} 不符"))
    return out
