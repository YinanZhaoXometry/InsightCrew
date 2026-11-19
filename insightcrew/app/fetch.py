"""fetch 命令：下载真实行情与证据到 data/market/real/。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py（新参数一律带缺省值）。
HTTP 与写盘的细节都在适配器里；本命令只做三件事：规范化用户输入、
把行情根目录 data/market 交给适配器、把结果翻译成中文输出与退出码。
每一类数据试哪些源、按什么顺序，由 SourceOrder 给出（BLUEPRINT 19.12 的 [sources]
默认值；WP31 把它接到设置里）；适配器层的 first_ok 按序尝试、把各家的失败聚成一条消息。
行情默认先试 Yahoo，Yahoo 不可用（常见是 HTTP 429 限流）就改用东方财富，两个源写出的
目录格式相同。

证据在行情之后下载（BLUEPRINT 18.12）：行情已写好的文件绝不因证据失败而回滚，
退出码也只看行情。美股的年报与申报文件走 SEC EDGAR，只在 .env 给了 IC_SEC_CONTACT
时访问；港股的公告走东方财富；港股没有免费年报源，不写 statements 文件（通道不可用）。
版本 2.2 的四类只在线上存在的数据（BLUEPRINT 19.12）默认关闭，作为关键字参数打开：
news 与 social 按代码下载并并入归档（按 url 去重，covers_to 前移，covers_from 留在
第一次写入那天，决策 47）；macro 按别名下载 FRED 序列（按（date, realtime_start）并集
保留历次 vintage）；markets 一天一份预测市场报价（只在 fetched_on 等于分析日期那天读）。
它们的失败同样只逐条说明，不改退出码。空的结果与失败的下载都不写文件；写文件用
临时名再改名，读的一方永远看不到半个文件。
"""
from __future__ import annotations

import datetime
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters import eastmoney, fred, news_rss, polymarket, reddit, stocktwits, yahoo
from ..adapters.eastmoney_ann import download_announcements
from ..adapters.edgar import client as evidence_client
from ..adapters.edgar import download_disclosures, download_statements
from ..adapters.vendors import first_ok
from ..domain import symbols
from ..domain.market import InvalidTicker, SourceUnavailable
from .settings import Settings
from .wiring import ROOT

_EVIDENCE_DAYS = 365          # 证据窗口的下载深度：一年，运行窗口（90 天）之外的日期也有得查
_MACRO_SERIES = ("fed_funds", "cpi", "unemployment", "us10y")   # 19.15 的 [macro] series 默认值；WP31 移入设置
_MARKETS_QUERY = "Federal Reserve"     # Polymarket 的检索词（宏观类市场；换词是一行改动）
_BARS_LABELS = {"yahoo": "Yahoo", "eastmoney": "东方财富"}
_KNOWN_SOURCES = {   # 每类数据认识的源；链里出现别的名字按用法错误当场拒绝（退出码 2）
    "bars": frozenset({"yahoo", "eastmoney"}),
    "announcements": frozenset({"sec", "eastmoney"}),
    "news": frozenset({"google_rss", "yahoo_rss"}),
    "social": frozenset({"stocktwits", "reddit"}),
    "macro": frozenset({"fred"}),
    "markets": frozenset({"polymarket"}),
}


@dataclass(frozen=True)
class SourceOrder:
    """六类数据各试哪些源、按什么顺序（BLUEPRINT 19.12 的 [sources] 默认值）。

    WP31 把它接到 insightcrew.toml 的 [sources]；在那之前它是 fetch 的参数缺省值。
    序列一律 tuple（约定：跨界的是元组）。
    """

    bars: tuple[str, ...] = ("yahoo", "eastmoney")
    announcements: tuple[str, ...] = ("sec", "eastmoney")
    news: tuple[str, ...] = ("google_rss", "yahoo_rss")
    social: tuple[str, ...] = ("stocktwits", "reddit")
    macro: tuple[str, ...] = ("fred",)
    markets: tuple[str, ...] = ("polymarket",)


def run(settings: Settings, tickers: tuple[str, ...], years: int, *,
        news: bool = False,
        social: bool = False,
        macro: bool = False,
        markets: bool = False,
        sources: SourceOrder | None = None) -> int:
    """下载给定的代码（连同各自的基准指数）几年的日线，再下载证据与（可选的）线上数据。

    先逐个规范化：不合格的代码当场拒绝（退出码 2），此时一个请求都没发、一个文件都没写；
    数据源链里出现未知的源名同样当场拒绝。合格的交给适配器，一次下载并写入
    data/market/real/（各 CSV 与 universe.toml）。行情按 sources.bars 的顺序尝试
    （缺省 Yahoo 在前，东方财富兜底），全都不可用时打印每家的原因并以退出码 1 结束——
    适配器保证失败不留半个文件；成功打印数据源、写出的每个序列与宇宙目录，然后下载
    证据与打开的四类线上数据（失败只逐条说明，不改退出码）。
    """
    normalized: list[str] = []
    for ticker in tickers:
        try:
            normalized.append(symbols.normalize(ticker))
        except InvalidTicker as e:
            print(f"拒绝下载：{e}。只接受美股代码（如 AAPL）与港股代码（如 0700.HK 或 700.HK）。")
            return 2

    order = sources if sources is not None else SourceOrder()
    for kind, known in _KNOWN_SOURCES.items():
        unknown = [name for name in getattr(order, kind) if name not in known]
        if unknown:
            print(f"拒绝下载：{kind} 数据源链里有未知的源 {unknown}。认识的源：{sorted(known)}")
            return 2

    market_root = ROOT / "data" / "market"
    bars: dict[str, Callable[[], tuple[str, ...]]] = {
        "yahoo": lambda: yahoo.fetch(normalized, years, market_root),
        "eastmoney": lambda: eastmoney.fetch(normalized, years, market_root,
                                             now=datetime.datetime.now(datetime.UTC)),
    }
    try:
        vendor, written = first_ok(tuple((name, bars[name]) for name in order.bars))
    except SourceUnavailable as failed:
        print(f"数据源不可用，没有写入任何文件：{failed}")
        return 1
    source = _BARS_LABELS[vendor]

    requested = set(normalized)
    for ticker in written:
        role = "请求" if ticker in requested else "基准"
        print(f"· {ticker}（{role}）")
    universe = (market_root / "real").relative_to(ROOT)
    print(f"宇宙 {universe}：{len(written)} 个 CSV 与 universe.toml 已写好（数据源：{source}）")

    with evidence_client() as client:
        if news or social or macro or markets:
            _download_live(client, tuple(normalized), market_root, order,
                           news=news, social=social, macro=macro, markets=markets)
        _download_evidence(settings, tuple(normalized), market_root, order)
    return 0


# ---- 线上数据（BLUEPRINT 19.12；默认关闭，见 run 的关键字参数）----


def _download_live(client, tickers: tuple[str, ...], market_root: Path, order: SourceOrder, *,
                   news: bool, social: bool, macro: bool, markets: bool) -> None:
    """下载四类只在线上存在的数据，写入 data/market/real/ 对应子目录（19.8 的路径）。

    每类按链上的顺序要第一家成功的（first_ok）；拿到后并入已有归档再原子落盘。
    任何一条失败或为空只打印原因（含"未配置密钥"），不改退出码，也不影响别的条目。
    """
    today = datetime.date.today()
    real = market_root / "real"
    for ticker in tickers:
        stem = symbols.file_stem(ticker)
        if news:
            _one_source(order.news, _news_calls(client, ticker, today),
                        f"{ticker} 新闻", real / "news" / f"{stem}.json", _merge_stream("items"))
        if social:
            _one_source(order.social, _social_calls(client, ticker, today),
                        f"{ticker} 社交", real / "social" / f"{stem}.json", _merge_stream("posts"))
    if macro:
        for alias in _MACRO_SERIES:
            _one_source(order.macro, _macro_calls(client, alias),
                        f"宏观 {alias}", real / "macro" / f"{alias}.json", _merge_observations)
    if markets:
        _one_source(order.markets, _markets_calls(client, today), "预测市场",
                    real / "markets" / f"{today.isoformat()}.json", None)


def _news_calls(client, ticker: str, today: datetime.date) -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "google_rss": lambda: news_rss.download_google(client, ticker, today),
        "yahoo_rss": lambda: news_rss.download_yahoo(client, ticker, today),
    }


def _social_calls(client, ticker: str,
                  today: datetime.date) -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "stocktwits": lambda: stocktwits.download_posts(client, ticker, today),
        "reddit": lambda: reddit.download_posts(client, ticker, today=today),
    }


def _macro_calls(client, alias: str) -> dict[str, Callable[[], dict[str, Any]]]:
    return {"fred": lambda: fred.download_series(client, alias)}


def _markets_calls(client, today: datetime.date) -> dict[str, Callable[[], dict[str, Any]]]:
    return {"polymarket": lambda: polymarket.download_markets(client, _MARKETS_QUERY, today=today)}


def _one_source(names: tuple[str, ...], calls: dict[str, Callable[[], dict[str, Any]]],
                label: str, path: Path,
                merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None) -> None:
    """按链取一份线上数据并落盘：全败只打印原因（first_ok 已把各家的原因聚好并脱敏）。"""
    try:
        vendor, fresh = first_ok(tuple((name, calls[name]) for name in names))
    except SourceUnavailable as failed:
        print(f"· {label}跳过：{failed}")
        return
    existing = _read_json(path)
    content = merge(existing, fresh) if existing is not None and merge is not None else fresh
    _write_json(path, content)
    count, unit = _entry_count(content)
    print(f"· {label} {count} {unit}（{vendor}）→ {path.relative_to(ROOT)}")


def _merge_stream(entries_key: str) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """news / social 归档的合并（19.8）：按 url 去重追加，covers_to 前移，
    covers_from 留在最早那天（决策 47：归档只从第一次写入那天起诚实覆盖）。"""

    def merge(existing: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
        entries = list(existing[entries_key])
        seen = {entry["url"] for entry in entries}
        entries += [entry for entry in fresh[entries_key] if entry["url"] not in seen]
        sources = existing["source"].split("+")
        return {"source": existing["source"] if fresh["source"] in sources
                else f"{existing['source']}+{fresh['source']}",
                "covers_from": min(existing["covers_from"], fresh["covers_from"]),
                "covers_to": max(existing["covers_to"], fresh["covers_to"]),
                entries_key: entries}

    return merge


def _merge_observations(existing: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """macro 归档的合并：按（date, realtime_start）取并集，历次 vintage 都留下，
    读取方按分析日期过滤 realtime_start，晚于它的行到不了任何阶段（不变量 6）。"""
    observations = list(existing["observations"])
    seen = {(row["date"], row["realtime_start"]) for row in observations}
    observations += [row for row in fresh["observations"]
                     if (row["date"], row["realtime_start"]) not in seen]
    return {"source": fresh["source"], "alias": fresh["alias"], "fred_id": fresh["fred_id"],
            "observations": observations}


def _entry_count(content: dict[str, Any]) -> tuple[int, str]:
    """归档里条目的个数与量词，用于输出（四类归档的键各不相同）。"""
    for key, unit in (("items", "条"), ("posts", "条"), ("observations", "行"), ("markets", "个")):
        if key in content:
            return len(content[key]), unit
    return 0, "条"


def _read_json(path: Path) -> dict[str, Any] | None:
    """读已有的归档；不存在或读不出来（损坏）都当作没有，整份用这次下载的内容重写。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, content: dict[str, Any]) -> None:
    """临时名写完再改名：读的一方永远看不到半个文件（与 _one 同一规矩）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp"
    temp.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


# ---- 证据（BLUEPRINT 18.12）----


def _download_evidence(settings: Settings, tickers: tuple[str, ...], market_root,
                       order: SourceOrder) -> None:
    """行情之后下载证据：每个请求的代码一份年报（美股）与一份公告（美股与港股）。

    任何一条证据失败或为空都不写文件、只打印原因；已写好的行情与别的证据不受影响。
    announcements 链在这里是适用性的筛选：SEC 只服务美股（且要联系方式），东方财富的
    公告列表只服务港股（端点按五位数字寻址），美股不向东方财富兜底——那只会发出一个
    不可能成功的请求。链上删掉某个源就跳过它那一侧。
    """
    contact = settings.sec_contact.get_secret_value() if settings.sec_contact else ""
    use_sec = "sec" in order.announcements
    if use_sec and not contact:
        print("SEC 证据跳过：.env 里没有 IC_SEC_CONTACT。美股的年报与申报文件不下载。")
    start = datetime.date.today() - datetime.timedelta(days=_EVIDENCE_DAYS)
    statements_dir = market_root / "real" / "statements"
    disclosures_dir = market_root / "real" / "disclosures"
    with evidence_client() as client:
        for ticker in tickers:
            if ticker.endswith(".HK"):
                if "eastmoney" in order.announcements:
                    _one(_hk_announcements(client, ticker, start), disclosures_dir)
                else:
                    print(f"· {ticker} 公告跳过：数据源链里没有 eastmoney。")
                continue
            if not contact or not use_sec:
                continue
            _one(_us_statements(client, ticker, contact), statements_dir)
            _one(_us_disclosures(client, ticker, contact, start), disclosures_dir)


def _us_statements(client, ticker: str, contact: str):
    try:
        out = download_statements(client, ticker, contact)
    except (SourceUnavailable, ValueError) as e:
        return (f"{ticker} 年报跳过：{e}", None, None, None)
    if out is None:
        return (f"{ticker} 年报跳过：不在 SEC 的代码表里，不是申报人。", None, None, None)
    stem, content = out
    if not content["rows"]:
        return (f"{ticker} 年报跳过：SEC 的响应里没有可用的年度报表行。", None, None, None)
    return (None, content, stem, f"{ticker} 年报 {len(content['rows'])} 行")


def _us_disclosures(client, ticker: str, contact: str, start: datetime.date):
    try:
        out = download_disclosures(client, ticker, contact, start)
    except (SourceUnavailable, ValueError) as e:
        return (f"{ticker} 申报文件跳过：{e}", None, None, None)
    if out is None:
        return (f"{ticker} 申报文件跳过：不在 SEC 的代码表里，不是申报人。", None, None, None)
    stem, content = out
    if not content["items"]:
        return (f"{ticker} 申报文件跳过：SEC 的响应里没有可用的申报文件。", None, None, None)
    return (None, content, stem, f"{ticker} 申报文件 {len(content['items'])} 条")


def _hk_announcements(client, ticker: str, start: datetime.date):
    try:
        stem, content = download_announcements(client, ticker, start)
    except SourceUnavailable as e:
        return (f"{ticker} 公告跳过：{e}", None, None, None)
    if not content["items"]:
        return (f"{ticker} 公告跳过：一年内没有任何公告。", None, None, None)
    return (None, content, stem, f"{ticker} 公告 {len(content['items'])} 条")


def _one(outcome: tuple[str | None, dict | None, str | None, str | None], folder) -> None:
    """把一次下载的结果落盘：失败或为空只打印原因，成功用临时名写完再改名。"""
    problem, content, stem, note = outcome
    if problem is not None or content is None or stem is None:
        print(f"· {problem}")
        return
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbols.file_stem(stem)}.json"
    temp = folder / f".{path.name}.tmp"
    temp.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    print(f"· {note} → {path.relative_to(ROOT)}")
