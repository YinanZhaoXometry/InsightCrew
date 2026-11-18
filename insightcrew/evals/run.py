"""eval 命令的分发：按名字运行离线评测。WP7 填写（WP8 在此加入 ablation）。

签名是约定：cli.py 已经按它调用；填写时保持签名，不改 cli.py。
evals 不得 import app：root 由命令行传入，需要开运行的评测由调用方交给它打开函数。
all 按顺序跑不需要模型的三项（inject、consistency、pit），三项都退出 0 才退出 0；
哪怕前一项失败，后面的项也要跑完，命令一次就能看到全部结果。
"""
from __future__ import annotations

from pathlib import Path

from . import consistency, inject, pit

_NO_MODEL = ("inject", "consistency", "pit")


def run(root: Path, name: str) -> int:
    """name 是 inject、consistency、pit、ablation 之一，或 all（跑其中不需要模型的三项）。"""
    if name == "all":
        codes = [_dispatch(root, item) for item in _NO_MODEL]
        return 0 if all(code == 0 for code in codes) else 1
    return _dispatch(root, name)


def _dispatch(root: Path, name: str) -> int:
    """按名字分发给具体的评测；ablation 留给 WP8，未知名字按未实现处理。"""
    if name == "inject":
        return inject.main()
    if name == "consistency":
        return consistency.main(root)
    if name == "pit":
        return pit.main(root)
    if name == "ablation":
        print("此命令尚未实现")            # WP8 加入：真实模型下的消融评测
        return 2
    print(f"未知评测 {name!r}：可用 inject、consistency、pit、ablation、all")
    return 2
