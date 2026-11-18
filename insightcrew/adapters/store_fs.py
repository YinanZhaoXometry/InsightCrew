"""运行目录：一次运行的全部产物写在这里，journal.jsonl 是其中唯一的事实来源。

目录名 <时刻>-<代码>-<分析日>-<4 位随机后缀>；随机后缀保证同一秒内启动的两次运行不互相覆盖。
"""
from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path

from ..domain.symbols import file_stem


def new_run_dir(root: Path, ticker: str, as_of: str, now: datetime) -> Path:
    """建一个新运行目录并返回。ticker 必须是规范化后的代码（intake 已保证），不含用户原文。"""
    path = root / f"{now:%Y%m%d-%H%M%S}-{file_stem(ticker)}-{as_of}-{secrets.token_hex(2)}"
    path.mkdir(parents=True)
    return path
