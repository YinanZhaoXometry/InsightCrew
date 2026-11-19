"""真实日线的下载适配器（WP9A）：这是唯一知道 Yahoo 端点与响应格式的代码。

fetch 把若干代码（连同各自从 symbols.benchmark_for 得到的基准指数）几年的日线
下载到 root/real/，写成每个代码一个 <file_stem>.csv 加一份 universe.toml，
与 WP2 的 CsvMarket 读的格式一致。端点是 unofficial 的，可能变更或限流：
HTTP 状态不对、响应不是 JSON、结构残缺、一根 bar 都没有，一律抛 SourceUnavailable。
先把全部序列下载并解析完，再逐个文件写临时文件后改名——任何失败都不会留下半个文件，
也不会留下引用着缺失 CSV 的 universe.toml。
"""
from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx2

from ..domain.market import Instrument, SourceUnavailable
from ..domain.symbols import benchmark_for
from .real_universe import COLUMNS, Market, plan, write

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}


def fetch(
    tickers: Sequence[str],
    years: int,
    root: str | Path,
    client: httpx2.Client | None = None,
) -> tuple[str, ...]:
    """下载几个代码（连同各自的基准指数）几年的日线到 root/real/，返回写出的代码。

    tickers 是已规范化的代码（用户输入先过 symbols.normalize）。client 缺省时自建、
    用完即关；传入的客户端由调用方负责关闭。任何失败抛 SourceUnavailable，一个文件都不写。
    """
    todo, markets = plan(tickers)
    if not todo:
        return ()
    if client is None:
        own_client = httpx2.Client()
        try:
            return _download_and_write(own_client, todo, markets, years, root)
        finally:
            own_client.close()     # httpx2 的 with 不走 close()，显式关才保证关闭
    return _download_and_write(client, todo, markets, years, root)


def _download_and_write(
    client: httpx2.Client,
    todo: Sequence[str],
    markets: dict[str, Market],
    years: int,
    root: str | Path,
) -> tuple[str, ...]:
    """先全部下载并解析（失败在这里抛出，还一个文件都没碰），再统一写盘。"""
    return write(root, [_download(client, ticker, years, markets[ticker]) for ticker in todo])


def _download(
    client: httpx2.Client, ticker: str, years: int, market: Market
) -> tuple[str, Instrument]:
    """下载并解析一个代码的日线，返回（CSV 文本，静态描述）。只算不写。"""
    url = f"{_BASE}/{quote(ticker, safe='')}?range={years}y&interval=1d"
    try:
        response = client.get(url, headers=_HEADERS)
    except httpx2.RequestError as e:
        raise SourceUnavailable(f"连不上 Yahoo 数据源：{e}（{ticker}）") from e
    if response.status_code // 100 != 2:
        raise SourceUnavailable(f"Yahoo 数据源不可用：HTTP {response.status_code}（{ticker}）")
    try:
        payload = response.json()
    except ValueError as e:
        raise SourceUnavailable(f"Yahoo 响应不是合法 JSON：{e}（{ticker}）") from e
    return _parse(ticker, payload, market)


def _parse(ticker: str, payload: object, market: Market) -> tuple[str, Instrument]:
    """读 chart.result[0]：timestamp 与 indicators.quote[0] 逐列对齐，meta 给静态描述。

    一根 bar 的日期是 timestamp+gmtoffset 的 UTC 日期。close 为 null 的行照写、
    close 记空（加载器会丢弃并计数）；close 有值而其他字段缺失的 bar 读不出来，按残缺处理。
    """
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise SourceUnavailable(f"Yahoo 响应里没有 chart 对象（{ticker}）")
    error = chart.get("error")
    if error is not None:
        detail = error.get("description", error) if isinstance(error, dict) else error
        raise SourceUnavailable(f"Yahoo 数据源拒绝了请求：{detail}（{ticker}）")
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise SourceUnavailable(f"Yahoo 响应里没有 chart.result[0]（{ticker}）")
    result = results[0]
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise SourceUnavailable(f"Yahoo 响应里没有 meta（{ticker}）")
    timestamps = result.get("timestamp")
    if not isinstance(timestamps, list) or not timestamps:
        raise SourceUnavailable(f"Yahoo 没有返回任何日线（{ticker}）：空下载不写盘")
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise SourceUnavailable(f"Yahoo 响应里没有 indicators.quote[0]（{ticker}）")
    fields = quotes[0]
    columns: list[list[Any]] = []
    for key in ("open", "high", "low", "close", "volume"):
        column = fields.get(key)
        if not isinstance(column, list) or len(column) != len(timestamps):
            raise SourceUnavailable(f"Yahoo 响应的行情数组缺失或与 timestamp 不一样长（{ticker}）")
        columns.append(column)
    offset = meta.get("gmtoffset")
    currency = meta.get("currency")
    name = meta.get("longName") or meta.get("shortName")
    if not isinstance(offset, (int, float)) or isinstance(offset, bool):
        raise SourceUnavailable(f"Yahoo 响应里没有 meta.gmtoffset（{ticker}）")
    if not isinstance(currency, str) or not currency:
        raise SourceUnavailable(f"Yahoo 响应里没有 meta.currency（{ticker}）")
    if not isinstance(name, str) or not name:
        raise SourceUnavailable(f"Yahoo 响应里没有 meta.longName/shortName（{ticker}）")

    opens, highs, lows, closes, volumes = columns
    lines = [COLUMNS]
    for ts, open_, high, low, close, volume in zip(
            timestamps, opens, highs, lows, closes, volumes, strict=True):
        day = datetime.datetime.fromtimestamp(ts + offset, tz=datetime.UTC).date()
        values = (open_, high, low, close, volume)
        if close is None:
            cells = ["" if value is None else str(value) for value in values]
        elif open_ is None or high is None or low is None or volume is None:
            raise SourceUnavailable(
                f"Yahoo 返回的 bar 缺少 open/high/low/volume（{ticker} {day.isoformat()}）")
        else:
            cells = [str(value) for value in values]      # 数值照数据源原样，不加工
        lines.append(f"{day.isoformat()},{','.join(cells)}")
    instrument = Instrument(ticker=ticker, name=name, market=market, currency=currency,
                            benchmark=benchmark_for(ticker), lot=0, synthetic=False)
    return "\n".join(lines) + "\n", instrument
