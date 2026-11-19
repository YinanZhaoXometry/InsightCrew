"""新闻与宏观的证据阶段（BLUEPRINT 19.12）。WP31 留桩：签名即最终签名，WP34 填正文。

news 通道同时覆盖新闻条目与宏观序列（macro 需要点名 news，prepare 处拒绝只开 macro）。
"""
from __future__ import annotations

from typing import Any

from ..flows.deps import Deps
from ..kernel.runtime import Run


def evidence(run: Run, deps: Deps, channels: tuple[str, ...]) -> None:
    """WP34 填写：读新闻窗口与宏观序列，写 news/items、news/card、news/status、
    macro/facts、macro/card、macro/markets、macro/status。桩把点名的通道记为不可用。
    """
    run.board.put("news/status", _off("news"), by="evidence")
    if "macro" in channels:
        run.board.put("macro/status", _off("macro"), by="evidence")


def _off(channel: str) -> dict[str, Any]:
    """通道不可用：只写状态与原因，此外一个键都不写（19.8 第 4 条）。"""
    return {"channel": channel, "state": "unavailable", "reason": "尚未接入（WP34 的桩）"}
