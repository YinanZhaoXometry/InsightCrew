"""fetch 命令：下载真实行情到 data/market/real/。WP5 留空，WP9B 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
HTTP 与写盘的细节都在 adapters.yahoo 里；本命令只做三件事：规范化用户输入、
把行情根目录 data/market 交给适配器、把结果翻译成中文输出与退出码。
"""
from __future__ import annotations

from ..adapters.yahoo import fetch as download
from ..domain import symbols
from ..domain.market import InvalidTicker, SourceUnavailable
from .settings import Settings
from .wiring import ROOT


def run(settings: Settings, tickers: tuple[str, ...], years: int) -> int:
    """下载给定的代码（连同各自的基准指数）几年的日线。

    先逐个规范化：不合格的代码当场拒绝（退出码 2），此时一个请求都没发、一个文件都没写。
    合格的交给适配器，一次下载并写入 data/market/real/（各 CSV 与 universe.toml）。
    数据源不可用时打印原因（HTTP 状态在原因里）并以退出码 1 结束——适配器保证失败不留半个文件；
    成功打印写出的每个序列与宇宙目录，返回 0。
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
        written = download(normalized, years, market_root)
    except SourceUnavailable as e:
        print(f"数据源不可用，没有写入任何文件：{e}")
        return 1

    requested = set(normalized)
    for ticker in written:
        role = "请求" if ticker in requested else "基准"
        print(f"· {ticker}（{role}）")
    universe = (market_root / "real").relative_to(ROOT)
    print(f"宇宙 {universe}：{len(written)} 个 CSV 与 universe.toml 已写好")
    return 0
