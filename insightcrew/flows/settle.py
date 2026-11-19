"""结算（BLUEPRINT 19.13）。WP31 留桩：签名即最终签名，WP35 填正文。"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.memory import OutcomeRecord
from ..flows.deps import Deps


def settle(deps: Deps, log: Any, known_by: date) -> list[OutcomeRecord]:
    """WP35 填写：结算每个持有期已走完的待决决策，两条路径一致才落账。
    桩不结算任何记录。log 是 DecisionLog（写入）或其按日志供给的替身（重放）。"""
    return []
