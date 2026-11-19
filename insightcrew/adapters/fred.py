# Adapted from TradingAgents (Apache-2.0), modified.
"""FRED 宏观序列的下载适配器（BLUEPRINT 19.12）：圣路易斯联储的免费 API，需要密钥。

密钥从环境变量 IC_FRED_API_KEY 读（.env 由设置层装进环境，本模块绝不自己解析 .env）；
没有密钥一个请求都不发，抛 NotConfigured——缺密钥不是数据问题（19.8）。下载结果转成
macro 归档形状（19.8）：
{"source", "alias", "fred_id", "observations": [{"date", "value", "realtime_start"}]}。
value 转成 float；"."（FRED 的缺失记号）与解析不了的行丢弃。归档不设 covers 窗口：每行
自带 realtime_start（它哪天发布），读取方按分析日期过滤，晚于分析日期的行到不了任何
阶段（不变量 6）。请求把 realtime_start=realtime_end 钉在 as_of（缺省今天，且不超过
FRED 自己的今天——美中时区，超了它用 400 拒绝），拿到的是"那天已经知道"的值，不是
后来的修正（vintage 钉住）。HTTP 400 带 FRED 自己的 error_message（多半是序列不存在）
抛 NotCovered；其它非 200 或不是 JSON 抛 SourceUnavailable——URL 里带着密钥，所有消息
先过 scrub 再抛（绝不打印带密钥的 URL）。观测窗口是 as_of 往前 look_back_days 天。

改编自 TradingAgents 的 vendors/fred.py：别名表与序列 ID 直通、vintage 钉住与美中时区
钳制、观测窗口参数、"." 缺失记号。序列元数据（标题与单位）的请求这里不做：归档形状
用不到，一次下载只发一个请求。
"""
from __future__ import annotations

import datetime
import os
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx2

from ..domain.market import NoData, NotConfigured, NotCovered, SourceUnavailable
from .vendors import scrub

_BASE = "https://api.stlouisfed.org/fred/series/observations"
_KEY_NAME = "IC_FRED_API_KEY"
_FRED_TZ = ZoneInfo("America/Chicago")     # FRED 的实时钟走圣路易斯（美中时区）
_DEFAULT_LOOKBACK = 365                    # 观测窗口：一年，能看清趋势与同比基期

# 常用别名 → FRED 序列 ID（改编自参考实现的别名表，收窄到本项目会用的）。
# 表外的短字母数字串当作原始 FRED 序列 ID 直通。
_ALIASES: dict[str, str] = {
    "fed_funds": "FEDFUNDS", "fed_funds_rate": "FEDFUNDS", "federal_funds_rate": "FEDFUNDS",
    "us2y": "DGS2", "2y_treasury": "DGS2",
    "us10y": "DGS10", "10y_treasury": "DGS10",
    "30y_treasury": "DGS30", "10y_2y_spread": "T10Y2Y", "yield_curve": "T10Y2Y",
    "cpi": "CPIAUCSL", "core_cpi": "CPILFESL",
    "pce": "PCEPI", "core_pce": "PCEPILFE", "inflation_expectations": "T10YIE",
    "real_gdp": "GDPC1", "gdp": "GDP", "industrial_production": "INDPRO",
    "unemployment": "UNRATE", "unemployment_rate": "UNRATE",
    "nonfarm_payrolls": "PAYEMS", "payrolls": "PAYEMS", "initial_claims": "ICSA",
    "m2": "M2SL", "money_supply": "M2SL", "vix": "VIXCLS", "dollar_index": "DTWEXBGS",
    "consumer_sentiment": "UMCSENT", "housing_starts": "HOUST", "retail_sales": "RSAFS",
}


def download_series(client: httpx2.Client, alias: str,
                    as_of: datetime.date | None = None,
                    look_back_days: int = _DEFAULT_LOOKBACK) -> dict[str, Any]:
    """按别名下载一条 FRED 序列，转成 macro 归档形状（19.8）。

    as_of 是分析日期：vintage 钉在它（缺省用 FRED 的今天），观测窗口是它往前
    look_back_days 天。没有密钥一个请求都不发，抛 NotConfigured。
    """
    key = os.getenv(_KEY_NAME, "")
    if not key:
        raise NotConfigured(f"没有配置 {_KEY_NAME}（.env），FRED 数据源未启用")
    series_id, normalized = _resolve(alias)
    pin = min(as_of, _fred_today()) if as_of is not None else _fred_today()
    url = _BASE + "?" + urlencode({
        "series_id": series_id,
        "observation_start": (pin - datetime.timedelta(days=look_back_days)).isoformat(),
        "observation_end": pin.isoformat(),
        "sort_order": "asc",
        "realtime_start": pin.isoformat(),
        "realtime_end": pin.isoformat(),
        "api_key": key,
        "file_type": "json",
    })
    body = _get(client, url)
    observations = [row for row in
                    (_observation(entry) for entry in body.get("observations") or [])
                    if row is not None]
    if not observations:
        raise NoData(f"FRED 的 {series_id} 在窗口内没有观测值（vintage {pin.isoformat()}）")
    return {"source": "FRED", "alias": normalized, "fred_id": series_id,
            "observations": observations}


# ---- 内部 ----


def _resolve(alias: str) -> tuple[str, str]:
    """别名 →（FRED 序列 ID, 规范别名）。既不是别名也不像序列 ID 的描述话抛 ValueError。"""
    key = alias.strip().lower().replace(" ", "_").replace("-", "_")
    if key in _ALIASES:
        return _ALIASES[key], key
    candidate = alias.strip().upper()
    if not candidate or len(candidate) > 30 or any(c.isspace() for c in candidate):
        raise ValueError(f"{alias!r} 既不是已知的宏观别名，也不是一个 FRED 序列 ID；"
                         "可用别名如 cpi、unemployment、fed_funds、us10y，或直接的序列 ID（如 CPIAUCSL）")
    return candidate, candidate.lower()


def _observation(entry: dict[str, Any]) -> dict[str, Any] | None:
    """一行观测 → {"date", "value", "realtime_start"}；"." 与解析不了的行返回 None。"""
    try:
        return {"date": str(entry["date"])[:10],
                "value": float(entry["value"]),
                "realtime_start": str(entry["realtime_start"])[:10]}
    except (KeyError, TypeError, ValueError):
        return None


def _fred_today() -> datetime.date:
    """FRED 自己的今天（美中时区）：realtime 钉的日子不能超过它，否则 FRED 用 400 拒绝。"""
    return datetime.datetime.now(_FRED_TZ).date()


def _get(client: httpx2.Client, url: str) -> dict[str, Any]:
    # URL 里带着密钥：下面的每条错误消息都先过 scrub 再抛，传输错误也不带异常链。
    try:
        response = client.get(url)
    except httpx2.HTTPError as e:
        raise SourceUnavailable(scrub(f"FRED 请求出错：{e}")) from None
    if response.status_code == 400:      # FRED 用 400 加 JSON 的 error_message 拒绝坏请求
        try:
            message = response.json().get("error_message", response.text)
        except ValueError:
            message = response.text
        raise NotCovered(scrub(f"FRED 拒绝了这个序列或参数：{message}（{url}）"))
    if response.status_code != 200:
        raise SourceUnavailable(scrub(f"FRED 请求失败：HTTP {response.status_code}（{url}）"))
    try:
        body = response.json()
    except ValueError as e:
        raise SourceUnavailable(scrub(f"FRED 响应不是 JSON：{url}")) from e
    if not isinstance(body, dict):
        raise SourceUnavailable(scrub(f"FRED 响应不是 JSON 对象：{url}"))
    return body
