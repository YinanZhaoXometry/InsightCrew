"""eval 命令的分发：按名字运行评测。WP7 填写，WP8 加入 ablation，WP19 加入 channels。

WP37（BLUEPRINT 19.16）把版本 2.2 的块加进了三个离线评测自身：inject 多了组合规则、
新闻与情绪引用和教训文字的注入块，consistency 多了工具家族与宏观变换的两路块，
pit 多了新闻/社交/宏观版本/预测市场/教训/工具事实的时点块——`all` 的三项没变，
新行随各评测自己打印。

evals 不得 import app：root 由命令行传入；ablation 与 channels 要开真实模型的运行，
开运行的函数、流程入口与设置里的流程配置由调用方交进来，没交就说明原因并以退出码 2 结束。
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
from . import ablation, channels, consistency, inject, pit

_NO_MODEL = ("inject", "consistency", "pit")


def run(root: Path, name: str, *, open_run: Callable[..., tuple[Run, Deps]] | None = None,
        analyze: Callable[..., Awaitable[Any]] | None = None, base: FlowConfig | None = None,
        no_model_reason: str = "") -> int:
    """name 是 inject、consistency、pit、ablation、channels 之一，或 all（跑不需要模型的三项）。"""
    if name == "all":
        codes = [_dispatch(root, item) for item in _NO_MODEL]
        return 0 if all(code == 0 for code in codes) else 1
    if name in ("ablation", "channels"):
        if open_run is None or analyze is None or base is None:
            print(f"该评测需要真实模型：{no_model_reason or '调用方没有提供开运行的函数'}")
            return 2
        module = ablation if name == "ablation" else channels
        return asyncio.run(module.main(open_run, analyze, base))
    if name == "v22":
        # WP38（BLUEPRINT 19.16、20.14 修正）：真实运行要调用方交来开运行的函数与流程入口；
        # 没交（离线、无密钥）就列出格子并说明原因，退出码 2。
        from . import v22
        if open_run is None or analyze is None:
            print(f"该评测需要真实模型：{no_model_reason or '调用方没有提供开运行的函数'}")
            print(f"v22 评测格子（{len(v22.cells())} 个）：")
            for cell in v22.cells():
                print(f"  {cell.name}: {cell.ticker} @{cell.as_of}（{cell.universe}）— {cell.about}")
            return 2
        return asyncio.run(v22.main(open_run, analyze, base))
    return _dispatch(root, name)


def _dispatch(root: Path, name: str) -> int:
    """按名字分发给不需要模型的评测；未知名字说明可用的名字。"""
    if name == "inject":
        return inject.main()
    if name == "consistency":
        return consistency.main(root)
    if name == "pit":
        return pit.main(root)
    print(f"未知评测 {name!r}：可用 inject、consistency、pit、ablation、channels、v22、all")
    return 2
