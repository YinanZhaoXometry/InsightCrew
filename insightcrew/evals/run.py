"""eval 命令的分发：按名字运行离线评测。WP5 留空，WP7 填写（WP8 在此加入 ablation）。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
evals 不得 import app：root 由命令行传入，需要开运行的评测由调用方交给它打开函数。
"""
from __future__ import annotations

from pathlib import Path


def run(root: Path, name: str) -> int:
    """name 是 inject、consistency、pit、ablation 之一，或 all（跑其中不需要模型的三项）。"""
    print("此命令尚未实现")
    return 2
