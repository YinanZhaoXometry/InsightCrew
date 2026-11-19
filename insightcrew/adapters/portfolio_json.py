"""组合文件的一次性读取：data/portfolios/*.json（BLUEPRINT 19.9）。

组合是运行开始前的输入：读不出、不合契约就在这里失败，绝不带着半懂的账目进流程。
未知字段是错误（契约封闭）；equity 必须大于 0；负的数量在本版本被拒绝（不支持空头）。
一切失败都是 ValueError，信息用中文并点名文件，调用方无须再包一层。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ..domain.portfolio import Portfolio


def load_portfolio(path: Path) -> Portfolio:
    """读一个组合 JSON 文件并校验。任何读不顺的地方都抛 ValueError，信息点名该文件。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"读不出组合文件 {path}：{e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"组合文件 {path} 不是合法的 JSON：{e}") from e
    try:
        portfolio = Portfolio.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"组合文件 {path} 不合契约：{e}") from e
    if portfolio.equity <= 0:
        raise ValueError(f"组合文件 {path} 的 equity 为 {portfolio.equity}，必须大于 0")
    for position in portfolio.positions:
        if position.quantity < 0:
            raise ValueError(f"组合文件 {path} 里 {position.ticker} 的 quantity 为 "
                             f"{position.quantity}；本版本不支持空头，负数在读取时拒绝")
    return portfolio
