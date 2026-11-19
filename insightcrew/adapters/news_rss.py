"""新闻 RSS 的下载适配器（BLUEPRINT 19.12）：Google News RSS 与 Yahoo Finance RSS。

两个源都不要密钥，也都只有最近几小时到几天的条目（18.18），所以 fetch 把每次下载并进
归档、按 url 去重（19.8）；本模块只把一次下载解析成 news 归档形状：
{"source", "covers_from", "covers_to", "items": [{"published", "source", "title", "url"}]}。
covers_from 与 covers_to 都是下载日：归档只从它第一次写入的那天起诚实覆盖（决策 47），
窗口在覆盖之外的读取方看到 NotCovered。published 是 ISO 日期或 null：pubDate 缺失或
解析不了存 null，由读取方丢掉并计数（19.8 规则 2）；没有 link 的条目留不下来（无法引用）。
每条 item 的 source 是发布者：Google News 的 <source> 元素给出名字，Yahoo 的条目没有这个
元素，就用 "Yahoo Finance"。失败（HTTP 非 200、响应不是 XML）抛 SourceUnavailable；
一条条目都没有抛 NoData，数据源链据此换下一家。本模块不做文件 I/O。
"""
from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx2

from insightcrew import __version__

from ..domain.market import NoData, SourceUnavailable

_GOOGLE_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
_YAHOO_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
_HEADERS = {"User-Agent": f"InsightCrew/{__version__}"}


def download_google(client: httpx2.Client, ticker: str,
                    today: datetime.date | None = None) -> dict[str, Any]:
    """Google News RSS：按代码搜新闻，转成 news 归档形状（19.8）。"""
    return _download(client, _GOOGLE_URL.format(query=quote(ticker)),
                     "Google News RSS", "Google News", ticker, today)


def download_yahoo(client: httpx2.Client, ticker: str,
                   today: datetime.date | None = None) -> dict[str, Any]:
    """Yahoo Finance RSS：按代码取新闻标题，转成 news 归档形状（19.8）。"""
    return _download(client, _YAHOO_URL.format(ticker=quote(ticker)),
                     "Yahoo Finance RSS", "Yahoo Finance", ticker, today)


# ---- 内部 ----


def _download(client: httpx2.Client, url: str, vendor: str, fallback: str,
              ticker: str, today: datetime.date | None) -> dict[str, Any]:
    """请求一个 RSS 源并解析成 news 归档形状；vendor 是归档的 source，fallback 是条目级 source 的缺省。"""
    body = _get(client, url, vendor)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise SourceUnavailable(f"{vendor} 响应不是 RSS/XML：{url}") from e
    items = [
        {"published": _published(element.findtext("pubDate")),
         "source": (element.findtext("source") or "").strip() or fallback,
         "title": (element.findtext("title") or "").strip(),
         "url": (element.findtext("link") or "").strip()}
        for element in root.iter("item")
    ]
    items = [item for item in items if item["url"]]     # 没有 link 的条目无法引用，不留
    if not items:
        raise NoData(f"{vendor} 没有返回 {ticker} 的任何新闻条目")
    day = (today or datetime.date.today()).isoformat()
    return {"source": vendor, "covers_from": day, "covers_to": day, "items": items}


def _published(text: str | None) -> str | None:
    """RFC 822 的 pubDate（如 "Thu, 19 Nov 2026 09:12:00 GMT"）→ ISO 日期；缺失或解析不了为 None。"""
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


def _get(client: httpx2.Client, url: str, vendor: str) -> str:
    try:
        response = client.get(url, headers=_HEADERS)
    except httpx2.HTTPError as e:
        raise SourceUnavailable(f"{vendor} 请求出错：{e}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"{vendor} 请求失败：HTTP {response.status_code}（{url}）")
    return response.text
