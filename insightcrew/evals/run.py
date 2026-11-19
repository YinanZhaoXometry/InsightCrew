"""eval 命令的分发：按名字运行评测。WP7 填写，WP8 加入 ablation。

evals 不得 import app：root 由命令行传入；ablation 要开真实模型的运行，开运行的函数、
流程入口与设置里的流程配置由调用方交进来，没交就说明原因并以退出码 2 结束。
all 按顺序跑不需要模型的三项（inject、consistency、pit），三项都退出 0 才退出 0；
哪怕前一项失败，后面的项也要跑完，命令一次就能看到全部结果。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.runtime import Run
from . import ablation, consistency, inject, pit

_NO_MODEL = ("inject", "consistency", "pit")


def run(root: Path, name: str, *, open_run: Callable[..., tuple[Run, Deps]] | None = None,
        analyze: Callable[..., Awaitable[Any]] | None = None, base: FlowConfig | None = None,
        no_model_reason: str = "") -> int:
    """name 是 inject、consistency、pit、ablation 之一，或 all（跑其中不需要模型的三项）。"""
    if name == "all":
        codes = [_dispatch(root, item) for item in _NO_MODEL]
        return 0 if all(code == 0 for code in codes) else 1
    if name == "ablation":
        if open_run is None or analyze is None or base is None:
            print(f"消融评测需要真实模型：{no_model_reason or '调用方没有提供开运行的函数'}")
            return 2
        return asyncio.run(ablation.main(open_run, analyze, base))
    return _dispatch(root, name)


def _dispatch(root: Path, name: str) -> int:
    """按名字分发给不需要模型的评测；未知名字说明可用的名字。"""
    if name == "inject":
        return inject.main()
    if name == "consistency":
        return consistency.main(root)
    if name == "pit":
        return pit.main(root)
    print(f"未知评测 {name!r}：可用 inject、consistency、pit、ablation、all")
    return 2
