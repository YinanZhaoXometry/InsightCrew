"""fetch 命令：下载真实行情到 data/market/real/。WP5 留空，WP9B 填写。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
"""
from __future__ import annotations

from .settings import Settings


def run(settings: Settings, tickers: tuple[str, ...], years: int) -> int:
    """下载给定的代码（连同各自的基准指数）几年的日线。"""
    print("此命令尚未实现")
    return 2
