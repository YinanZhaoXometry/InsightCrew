# Adapted from TradingAgents (Apache-2.0), modified.
"""StockTwits 公开贴文流的下载适配器（BLUEPRINT 19.12）：按代码取最近的贴文。

端点 api.stocktwits.com/api/2/streams/symbol/{代码}.json 不要密钥；每条贴文带用户自标
的多空标签（Bullish / Bearish，没有就是 null）、正文（HTML 转义过）、时间与作者。
下载结果转成 social 归档形状（19.8）：
{"source", "covers_from", "covers_to", "posts": [{"published", "source", "text", "url", "tag"}]}。
published 是 created_at 的日期部分（源给的本地日期）或 null；source 是 "@作者名"；
text 是反转义后的正文；url 是 StockTwits 的贴文固定链接（作者名加贴文 id 拼成，没有
id 的贴文留不下来）；tag 是用户自标的多空标签，没有为 null。covers_from 与 covers_to
都是下载日（决策 47），归档合并与按 url 去重由 fetch 做。HTTP 非 200 或响应不是 JSON 抛
SourceUnavailable；一条贴文都没有抛 NoData，数据源链据此换下一家。本模块不做文件 I/O。

改编自 TradingAgents 的 vendors/stocktwits.py：贴文流端点与响应形状、正文的 HTML 反转义、
情绪标签的位置（entities.sentiment.basic）。它对加密货币代码的映射这里不需要（本系统只
分析美股与港股），错误也不吞成占位字符串，而是抛本项目的类型化错误。
"""
from __future__ import annotations

import datetime
import html
from typing import Any
from urllib.parse import quote

import httpx2

from insightcrew import __version__

from ..domain.market import NoData, SourceUnavailable

_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_HEADERS = {"User-Agent": f"InsightCrew/{__version__}"}


def download_posts(client: httpx2.Client, ticker: str,
                   today: datetime.date | None = None) -> dict[str, Any]:
    """按代码取 StockTwits 最近的贴文，转成 social 归档形状（19.8）。"""
    url = _URL.format(symbol=quote(ticker.upper()))
    body = _get(client, url)
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise SourceUnavailable(f"StockTwits 响应形状不对（没有 messages 列表）：{url}")
    posts = [post for post in (_post(message) for message in messages) if post["url"]]
    if not posts:
        raise NoData(f"StockTwits 没有 {ticker} 的任何贴文")
    day = (today or datetime.date.today()).isoformat()
    return {"source": "StockTwits", "covers_from": day, "covers_to": day, "posts": posts}


# ---- 内部 ----


def _post(message: dict[str, Any]) -> dict[str, Any]:
    """一条贴文 → social 归档的一行；url 为空（没有 id 或作者名）的由调用方丢弃。"""
    user = str(((message.get("user") or {}).get("username")) or "").strip()
    sentiment = ((message.get("entities") or {}).get("sentiment") or {}).get("basic")
    identifier = message.get("id")
    return {
        "published": _published(message.get("created_at")),
        "source": f"@{user}" if user else "StockTwits",
        "text": html.unescape(str(message.get("body") or "")),
        "url": (f"https://stocktwits.com/{user}/message/{identifier}"
                if user and identifier is not None else ""),
        "tag": sentiment or None,
    }


def _published(text: Any) -> str | None:
    """created_at（ISO 8601，带时区）的日期部分；缺失或解析不了返回 None。"""
    try:
        return datetime.date.fromisoformat(str(text)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _get(client: httpx2.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url, headers=_HEADERS)
    except httpx2.HTTPError as e:
        raise SourceUnavailable(f"StockTwits 请求出错：{e}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"StockTwits 请求失败：HTTP {response.status_code}（{url}）")
    try:
        body = response.json()
    except ValueError as e:
        raise SourceUnavailable(f"StockTwits 响应不是 JSON：{url}") from e
    if not isinstance(body, dict):
        raise SourceUnavailable(f"StockTwits 响应不是 JSON 对象：{url}")
    return body
