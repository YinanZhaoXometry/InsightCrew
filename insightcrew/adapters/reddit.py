# Adapted from TradingAgents (Apache-2.0), modified.
"""Reddit 公开 Atom 搜索源的下载适配器（BLUEPRINT 19.12）：按代码搜财经版块的帖子。

Reddit 的 JSON 搜索接口对匿名客户端返回 403（WAF），公开的 RSS/Atom 搜索源是唯一出路，
它不带分数或评论数；匿名 RSS 大约每 IP 每分钟一次，所以几个版块合并成一次请求
（r/a+b+c）。下载结果转成 social 归档形状（19.8）：
{"source", "covers_from", "covers_to", "posts": [{"published", "source", "text", "url", "tag"}]}。
published 是 Atom published 的日期或 null；source 是 "r/版块名"（条目的 category 给出，
给不出就用唯一的那个请求版块，再不行记 unknown）；text 是标题加换行加正文（正文剥掉
HTML：Reddit 把自述文字包在 SC_OFF/SC_ON 注释之间，两层转义都解开）；url 是条目链接，
没有的不留；tag 恒为 null（Reddit 没有情绪标签）。covers_from 与 covers_to 都是下载日
（决策 47），归档合并与按 url 去重由 fetch 做。HTTP 非 200 或响应不是 XML 抛
SourceUnavailable；一条帖子都没有抛 NoData。429 不退避重试：那是线上调优的事，失败就
换下一家。本模块不做文件 I/O。

改编自 TradingAgents 的 vendors/reddit.py：搜索源的端点形状、SC_OFF/SC_ON 的处理、按
条目 category 分版块、一次合并多个版块。它对加密货币版块的映射与 429 退避这里不要。
"""
from __future__ import annotations

import datetime
import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

import httpx2

from insightcrew import __version__

from ..domain.market import NoData, SourceUnavailable

_URL = "https://www.reddit.com/r/{subs}/search.rss?{query}"
_ATOM = {"atom": "http://www.w3.org/2005/Atom"}
_HEADERS = {"User-Agent": f"InsightCrew/{__version__}"}
_DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")   # 按讨论密度排序，改编自参考实现


def download_posts(client: httpx2.Client, ticker: str,
                   subreddits: Sequence[str] = _DEFAULT_SUBREDDITS,
                   today: datetime.date | None = None) -> dict[str, Any]:
    """按代码在几个财经版块搜最近的帖子（合并成一次请求），转成 social 归档形状（19.8）。"""
    subs = "+".join(subreddits)
    url = _URL.format(subs=subs, query=urlencode(
        {"q": ticker, "restrict_sr": "on", "sort": "new", "t": "week", "limit": 100}))
    body = _get(client, url)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise SourceUnavailable(f"Reddit 响应不是 Atom/XML：{url}") from e
    posts = [post for post in
             (_post(element, subreddits) for element in root.findall("atom:entry", _ATOM))
             if post["url"]]
    if not posts:
        raise NoData(f"Reddit 没有提到 {ticker} 的任何帖子（{subs}）")
    day = (today or datetime.date.today()).isoformat()
    return {"source": "Reddit", "covers_from": day, "covers_to": day, "posts": posts}


# ---- 内部 ----


def _post(entry: ET.Element, subreddits: Sequence[str]) -> dict[str, Any]:
    """一条 Atom 条目 → social 归档的一行；url 为空的由调用方丢弃。"""
    title = (entry.findtext("atom:title", namespaces=_ATOM) or "").strip()
    body = _plain(entry.findtext("atom:content", namespaces=_ATOM))
    link = entry.find("atom:link", _ATOM)
    category = entry.find("atom:category", _ATOM)
    board = ((category.get("term") or "").strip() if category is not None else "") or (
        subreddits[0] if len(subreddits) == 1 else "unknown")
    return {
        "published": _published(entry.findtext("atom:published", namespaces=_ATOM)),
        "source": f"r/{board}",
        "text": f"{title}\n{body}" if body else title,
        "url": ((link.get("href") or "").strip() if link is not None else ""),
        "tag": None,
    }


def _plain(content: str | None) -> str:
    """Atom 条目的 HTML 正文 → 纯文本：取 SC_OFF/SC_ON 之间，剥标签，反转义，折叠空白。"""
    if not content:
        return ""
    if "<!-- SC_OFF -->" in content and "<!-- SC_ON -->" in content:
        content = content.split("<!-- SC_OFF -->")[1].split("<!-- SC_ON -->")[0]
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", content)).split())


def _published(text: str | None) -> str | None:
    """Atom published（ISO 8601）的日期；缺失或解析不了返回 None。"""
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _get(client: httpx2.Client, url: str) -> str:
    try:
        response = client.get(url, headers=_HEADERS)
    except httpx2.HTTPError as e:
        raise SourceUnavailable(f"Reddit 请求出错：{e}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"Reddit 请求失败：HTTP {response.status_code}（{url}）")
    return response.text
