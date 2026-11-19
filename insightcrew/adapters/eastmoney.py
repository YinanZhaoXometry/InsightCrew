"""真实日线的第二个下载适配器：东方财富 push2his 的日 K 线接口。唯一知道这个端点的代码。

Yahoo 限流（HTTP 429）时由 fetch 命令改用它。不要 key，美股、港股与两个基准指数都有，
价格不复权（fqt=0），与 BLUEPRINT 6.3 规则 8 "价格按报告原样使用" 一致。写出的目录与
yahoo 适配器完全相同（real_universe.write），CsvMarket 分不出数据来自哪个源。

与 Yahoo 的两处不同：
- 接口按"市场号.代码"寻址（secid）。港股 116.<五位代码>；两个基准有固定的 secid；
  其余美股依次试纳斯达克 105、纽交所 106、美国证交所 107，`data` 为 null 就换下一个。
- 接口把进行中的交易日也当成一根有收盘价的 K 线返回。本地日期为今天、且当地时间未过
  收盘后半小时的那一根，按"未结算"写成收盘价为空的行，加载器把它丢弃并计数（6.3 规则 2）。
"""
from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from ..domain.market import Instrument, SourceUnavailable
from ..domain.symbols import benchmark_for
from .real_universe import COLUMNS, Market, plan, write

_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_FIELDS = "fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0"   # 日期,开,收,高,低,量；不复权
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
_TIMEOUT = 20.0
_BENCHMARKS = {"SPY": "107.SPY", "^HSI": "100.HSI"}
_US_MARKETS = ("105", "106", "107")
_CURRENCY: dict[Market, str] = {"US": "USD", "HK": "HKD"}
_SETTLED_AFTER: dict[Market, tuple[ZoneInfo, datetime.time]] = {
    "US": (ZoneInfo("America/New_York"), datetime.time(16, 30)),
    "HK": (ZoneInfo("Asia/Hong_Kong"), datetime.time(16, 30)),
}


def fetch(
    tickers: Sequence[str],
    years: int,
    root: str | Path,
    *,
    now: datetime.datetime,
    client: httpx2.Client | None = None,
) -> tuple[str, ...]:
    """下载几个代码（连同各自的基准指数）几年的日线到 root/real/，返回写出的代码。

    tickers 是已规范化的代码。now 是带时区的当前时刻，由调用方读一次传进来，
    用来算起始日期和判断最后一根 K 线是否已结算。任何失败抛 SourceUnavailable，一个文件都不写。
    """
    todo, markets = plan(tickers)
    if not todo:
        return ()
    if client is None:
        own_client = httpx2.Client(timeout=_TIMEOUT)
        try:
            return write(root, [_download(own_client, t, years, markets[t], now) for t in todo])
        finally:
            own_client.close()
    return write(root, [_download(client, t, years, markets[t], now) for t in todo])


def _secids(ticker: str) -> tuple[str, ...]:
    if ticker in _BENCHMARKS:
        return (_BENCHMARKS[ticker],)
    if ticker.endswith(".HK"):
        return (f"116.{ticker[:-3].zfill(5)}",)
    if ticker.startswith("^"):
        raise SourceUnavailable(f"东方财富数据源没有指数 {ticker} 的对应代码")
    return tuple(f"{market}.{ticker}" for market in _US_MARKETS)


def _download(
    client: httpx2.Client, ticker: str, years: int, market: Market, now: datetime.datetime
) -> tuple[str, Instrument]:
    """依次试该代码的每个 secid，第一个有数据的就是它。只算不写。"""
    zone, _ = _SETTLED_AFTER[market]
    today = now.astimezone(zone).date()
    begin = today - datetime.timedelta(days=round(365.25 * years))
    for secid in _secids(ticker):
        url = f"{_BASE}?secid={secid}&{_FIELDS}&beg={begin:%Y%m%d}&end={today:%Y%m%d}"
        try:
            response = client.get(url, headers=_HEADERS)
        except httpx2.RequestError as e:
            raise SourceUnavailable(f"连不上东方财富数据源：{e}（{ticker}）") from e
        if response.status_code // 100 != 2:
            raise SourceUnavailable(f"东方财富数据源不可用：HTTP {response.status_code}（{ticker}）")
        try:
            payload = response.json()
        except ValueError as e:
            raise SourceUnavailable(f"东方财富响应不是合法 JSON：{e}（{ticker}）") from e
        data = payload.get("data") if isinstance(payload, dict) else None
        if data is None:
            continue                                          # 这个市场号下没有这个代码
        return _parse(ticker, data, market, now)
    raise SourceUnavailable(f"东方财富数据源找不到 {ticker}")


def _parse(ticker: str, data: object, market: Market, now: datetime.datetime) -> tuple[str, Instrument]:
    """读 data.name 与 data.klines（每行 "日期,开,收,高,低,量"），排成 CsvMarket 的列顺序。"""
    if not isinstance(data, dict):
        raise SourceUnavailable(f"东方财富响应里的 data 不是对象（{ticker}）")
    name = data.get("name")
    klines = data.get("klines")
    if not isinstance(name, str) or not name:
        raise SourceUnavailable(f"东方财富响应里没有 name（{ticker}）")
    if not isinstance(klines, list) or not klines:
        raise SourceUnavailable(f"东方财富没有返回任何日线（{ticker}）：空下载不写盘")

    zone, settled_after = _SETTLED_AFTER[market]
    local = now.astimezone(zone)
    lines = [COLUMNS]
    for line in klines:
        cells = line.split(",") if isinstance(line, str) else []
        if len(cells) < 6:
            raise SourceUnavailable(f"东方财富返回的 K 线行残缺：{line!r}（{ticker}）")
        day_text, open_, close, high, low, volume = cells[:6]
        try:
            day = datetime.date.fromisoformat(day_text)
            for value in (open_, close, high, low, volume):
                float(value)
        except ValueError as e:
            raise SourceUnavailable(f"东方财富返回的 K 线行读不出来：{line!r}（{ticker}）") from e
        unsettled = day > local.date() or (day == local.date() and local.time() < settled_after)
        if unsettled:
            lines.append(f"{day.isoformat()},,,,,")
        else:
            lines.append(f"{day.isoformat()},{open_},{high},{low},{close},{volume}")
    instrument = Instrument(ticker=ticker, name=name, market=market, currency=_CURRENCY[market],
                            benchmark=benchmark_for(ticker), lot=0, synthetic=False)
    return "\n".join(lines) + "\n", instrument
