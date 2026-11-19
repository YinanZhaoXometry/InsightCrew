"""东方财富公告列表的下载适配器（BLUEPRINT 18.12）：唯一知道这个非官方端点的代码。

港股公告没有免费官方归档源，这里用东方财富的公告列表补上 disclosures 通道的港股一侧。
分页从新到旧读，直到某一页出现早于请求起点的日期（说明起点已翻过）、或页为空、
或读满 20 页。下载结果转成 disclosures JSON 文件的内容（18.7 的格式），由 fetch 落盘；
本模块不做文件 I/O。失败（HTTP 非 200、响应形状不对）抛 SourceUnavailable，状态码在消息里。
端点是 unofficial 的：结构随时可能变，变了就在这里改。
"""
from __future__ import annotations

import datetime
from typing import Any

from ..domain.market import SourceUnavailable

_URL = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
        "?sr=-1&page_size=50&page_index={page}&ann_type=H&client_source=web&stock_list={code}")
_MAX_PAGES = 20


def download_announcements(client, ticker: str, start: datetime.date,
                           today: datetime.date | None = None) -> tuple[str, dict[str, Any]]:
    """下载一个港股代码的公告列表，转成 disclosures JSON 的内容。

    ticker 是 0700.HK 这类规范化代码；端点要五位数字（00700）。窗口起点 start 用于
    决定翻页停在哪：翻到了它，covers_from 就是它；翻不到（读尽 20 页或空页），
    covers_from 是读到的最老日期，调用方据此知道覆盖不足。
    """
    code = ticker.split(".", 1)[0].zfill(5)
    items: list[dict[str, Any]] = []
    oldest: datetime.date | None = None
    reached_start = False
    for page in range(1, _MAX_PAGES + 1):
        body = _get(client, page, code)
        listing = ((body or {}).get("data") or {}).get("list") or []
        if not listing:
            break
        for entry in listing:
            day = _day(entry.get("notice_date"))
            if day is None:
                continue
            columns = entry.get("columns") or []
            kind = columns[0].get("column_name", "") if columns and isinstance(columns[0], dict) \
                else ""
            items.append({"published": day.isoformat(), "kind": kind,
                          "title": entry.get("title", ""), "ref": entry.get("art_code", "")})
            oldest = day if oldest is None or day < oldest else oldest
            if day < start:
                reached_start = True
        if reached_start:
            break
    return ticker, {
        "source": "东方财富",
        "covers_from": (start if reached_start and oldest is not None and oldest <= start
                        else (oldest or start)).isoformat(),
        "covers_to": (today or datetime.date.today()).isoformat(),
        "items": items,
    }


# ---- 内部 ----


def _get(client, page: int, code: str) -> Any:
    response = client.get(_URL.format(page=page, code=code))
    if response.status_code != 200:
        raise SourceUnavailable(f"东方财富公告请求失败：HTTP {response.status_code}（第 {page} 页）")
    try:
        return response.json()
    except ValueError as e:
        raise SourceUnavailable("东方财富公告响应不是 JSON") from e


def _day(text: Any) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None
