# Adapted from TradingAgents (Apache-2.0), modified.
"""Polymarket 预测市场的下载适配器（BLUEPRINT 19.12）：Gamma 公开搜索接口，不要密钥。

预测市场的报价是"人群定价的概率"，是源的原文，不是事实（决策 48）：归档形状只有
{"source", "fetched_on", "markets": [{"question", "probability", "volume", "end_date"}]}。
只留还没结算、截止日不早于今天、且 outcomes 与 outcomePrices 都解析得出来的市场（Gamma
把这两个字段编码成 JSON 字符串），按成交额从大到小排，最多 limit 条。probability 是
第一个结果（通常是 Yes）的价格；volume 是 volumeNum；end_date 取 endDate 的前十个字符。
市场只有当前一刻的报价、没有历史（决策 47），所以文件按天存（19.8），读取方只在
fetched_on 等于分析日期那天用它。HTTP 非 200 或不是 JSON 抛 SourceUnavailable；一个
可用的市场都没有抛 NoData，数据源链据此换下一家。本模块不做文件 I/O。

改编自 TradingAgents 的 vendors/polymarket.py：Gamma 搜索接口的形状、outcomes 与
outcomePrices 的 JSON 字符串编码、"closed 才是已结算的可靠标记"、按 volumeNum 排序。
它的概率格式化与一周涨跌这里用不上（归档存原始数）。
"""
from __future__ import annotations

import datetime
import json
from typing import Any
from urllib.parse import quote

import httpx2

from insightcrew import __version__

from ..domain.market import NoData, SourceUnavailable

_URL = "https://gamma-api.polymarket.com/public-search?q={query}&limit_per_type=20"
_HEADERS = {"User-Agent": f"InsightCrew/{__version__}"}
_DEFAULT_LIMIT = 6        # 归档里留成交额最大的几个，改编自参考实现的默认值


def download_markets(client: httpx2.Client, query: str, today: datetime.date | None = None,
                     limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
    """按主题搜开放的预测市场，转成 markets 归档形状（19.8）。"""
    day = today or datetime.date.today()
    body = _get(client, _URL.format(query=quote(query)))
    candidates = [market for event in body.get("events") or [] if isinstance(event, dict)
                  for market in event.get("markets") or []
                  if _forward_looking(market, day)]
    candidates.sort(key=lambda market: market.get("volumeNum") or 0, reverse=True)
    markets = [row for row in (_row(market) for market in candidates[:limit]) if row is not None]
    if not markets:
        raise NoData(f"Polymarket 没有匹配 {query!r} 的开放预测市场")
    return {"source": "Polymarket", "fetched_on": day.isoformat(), "markets": markets}


# ---- 内部 ----


def _forward_looking(market: Any, today: datetime.date) -> bool:
    """只留还没结算、截止日不早于今天、且两个 JSON 字符串字段都解析得出的市场。

    closed 是已结算的可靠标记（结算后 active 仍是 true）；截止日解析不出来也不能保证
    还没结算，一律不留。"""
    if not isinstance(market, dict) or market.get("closed"):
        return False
    try:
        end = datetime.date.fromisoformat(str(market.get("endDate") or "")[:10])
    except ValueError:
        return False
    if end < today:
        return False
    return bool(_json_list(market.get("outcomes"))) and bool(_json_list(market.get("outcomePrices")))


def _row(market: dict[str, Any]) -> dict[str, Any] | None:
    """一个市场 → markets 归档的一行；第一个结果的价格解析不出来返回 None。"""
    prices = _json_list(market.get("outcomePrices"))
    try:
        probability = float(prices[0])
    except (IndexError, TypeError, ValueError):
        return None
    return {"question": str(market.get("question") or ""),
            "probability": probability,
            "volume": float(market.get("volumeNum") or 0),
            "end_date": str(market.get("endDate") or "")[:10]}


def _json_list(value: Any) -> list[Any]:
    """Gamma 把 outcomes / outcomePrices 编码成 JSON 字符串；列表原样通过，坏值给空表。"""
    if isinstance(value, list):
        return value
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _get(client: httpx2.Client, url: str) -> dict[str, Any]:
    try:
        response = client.get(url, headers=_HEADERS)
    except httpx2.HTTPError as e:
        raise SourceUnavailable(f"Polymarket 请求出错：{e}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"Polymarket 请求失败：HTTP {response.status_code}（{url}）")
    try:
        body = response.json()
    except ValueError as e:
        raise SourceUnavailable(f"Polymarket 响应不是 JSON：{url}") from e
    if not isinstance(body, dict):
        raise SourceUnavailable(f"Polymarket 响应不是 JSON 对象：{url}")
    return body
