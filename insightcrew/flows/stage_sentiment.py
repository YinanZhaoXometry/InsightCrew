"""社交情感的证据阶段（BLUEPRINT 19.12）。WP31 留桩：签名即最终签名，WP34 填正文。"""
from __future__ import annotations

from typing import Any

from ..flows.deps import Deps
from ..kernel.runtime import Run


def evidence(run: Run, deps: Deps, channels: tuple[str, ...]) -> None:
    """WP34 填写：读社交窗口，写 sentiment/posts、sentiment/card、sentiment/status。
    桩把点名的通道记为不可用。
    """
    run.board.put("sentiment/status", _off(), by="evidence")


def _off() -> dict[str, Any]:
    return {"channel": "sentiment", "state": "unavailable", "reason": "尚未接入（WP34 的桩）"}
