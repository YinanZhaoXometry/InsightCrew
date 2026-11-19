"""fetch 命令：下载真实行情与证据到 data/market/real/。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
HTTP 与写盘的细节都在适配器里；本命令只做三件事：规范化用户输入、
把行情根目录 data/market 交给适配器、把结果翻译成中文输出与退出码。
行情先试 Yahoo；Yahoo 不可用（常见是 HTTP 429 限流）就改用东方财富，两个源写出的目录格式相同。

证据在行情之后下载（BLUEPRINT 18.12）：行情已写好的文件绝不因证据失败而回滚，
退出码也只看行情。美股的年报与申报文件走 SEC EDGAR，只在 .env 给了 IC_SEC_CONTACT
时访问；港股的公告走东方财富；港股没有免费年报源，不写 statements 文件（通道不可用）。
空的结果与失败的下载都不写文件；写文件用临时名再改名，读的一方永远看不到半个文件。
"""
from __future__ import annotations

import datetime
import json

from ..adapters import eastmoney, yahoo
from ..adapters.eastmoney_ann import download_announcements
from ..adapters.edgar import client as evidence_client
from ..adapters.edgar import download_disclosures, download_statements
from ..domain import symbols
from ..domain.market import InvalidTicker, SourceUnavailable
from .settings import Settings
from .wiring import ROOT

_EVIDENCE_DAYS = 365          # 证据窗口的下载深度：一年，运行窗口（90 天）之外的日期也有得查


def run(settings: Settings, tickers: tuple[str, ...], years: int) -> int:
    """下载给定的代码（连同各自的基准指数）几年的日线，再下载证据。

    先逐个规范化：不合格的代码当场拒绝（退出码 2），此时一个请求都没发、一个文件都没写。
    合格的交给适配器，一次下载并写入 data/market/real/（各 CSV 与 universe.toml）。
    行情两个数据源都不可用时打印两条原因并以退出码 1 结束——适配器保证失败不留半个文件；
    成功打印数据源、写出的每个序列与宇宙目录，然后下载证据（失败只逐条说明，不改退出码）。
    """
    normalized: list[str] = []
    for ticker in tickers:
        try:
            normalized.append(symbols.normalize(ticker))
        except InvalidTicker as e:
            print(f"拒绝下载：{e}。只接受美股代码（如 AAPL）与港股代码（如 0700.HK 或 700.HK）。")
            return 2

    market_root = ROOT / "data" / "market"
    try:
        written = yahoo.fetch(normalized, years, market_root)
        source = "Yahoo"
    except SourceUnavailable as first:
        print(f"{first}；改用东方财富数据源。")
        try:
            written = eastmoney.fetch(normalized, years, market_root,
                                      now=datetime.datetime.now(datetime.UTC))
            source = "东方财富"
        except SourceUnavailable as second:
            print(f"数据源不可用，没有写入任何文件：{second}")
            return 1

    requested = set(normalized)
    for ticker in written:
        role = "请求" if ticker in requested else "基准"
        print(f"· {ticker}（{role}）")
    universe = (market_root / "real").relative_to(ROOT)
    print(f"宇宙 {universe}：{len(written)} 个 CSV 与 universe.toml 已写好（数据源：{source}）")

    _download_evidence(settings, tuple(normalized), market_root)
    return 0


# ---- 证据（BLUEPRINT 18.12）----


def _download_evidence(settings: Settings, tickers: tuple[str, ...], market_root) -> None:
    """行情之后下载证据：每个请求的代码一份年报（美股）与一份公告（美股与港股）。

    任何一条证据失败或为空都不写文件、只打印原因；已写好的行情与别的证据不受影响。
    """
    contact = settings.sec_contact.get_secret_value() if settings.sec_contact else ""
    if not contact:
        print("SEC 证据跳过：.env 里没有 IC_SEC_CONTACT。美股的年报与申报文件不下载。")
    start = datetime.date.today() - datetime.timedelta(days=_EVIDENCE_DAYS)
    statements_dir = market_root / "real" / "statements"
    disclosures_dir = market_root / "real" / "disclosures"
    with evidence_client() as client:
        for ticker in tickers:
            if ticker.endswith(".HK"):
                _one(_hk_announcements(client, ticker, start), disclosures_dir)
                continue
            if not contact:
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
