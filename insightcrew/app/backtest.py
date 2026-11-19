"""backtest 命令（BLUEPRINT 19.13）。WP31 留桩：WP35 填正文。"""
from __future__ import annotations

from .settings import Settings


def run(settings: Settings, tickers: tuple[str, ...], from_date: str, to_date: str,
        every: int, run_id: str, yes: bool) -> int:
    """WP35 填写：按交易日网格逐日先结算再分析（记忆开），进度可续跑；真实模型要 --yes。"""
    raise NotImplementedError("回测命令尚未实现（WP35 的桩）")
