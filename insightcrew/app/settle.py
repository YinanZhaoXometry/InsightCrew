"""settle 命令（BLUEPRINT 19.13）。WP31 留桩：WP35 填正文。"""
from __future__ import annotations

from .settings import Settings


def run(settings: Settings, known_by: str) -> int:
    """WP35 填写：结算到 known_by 为止已走完持有期的决策，并让 reflector 写课程。"""
    raise NotImplementedError("结算命令尚未实现（WP35 的桩）")
