"""行情数据的契约、端口与数据错误。

Bar 是普通冻结 dataclass：它持有真实的日期，只活在 domain 内部，不上黑板。
Instrument 是契约（冻结且封闭）。六个错误按调用方必须做出的反应命名
（见 BLUEPRINT 6.3、18.5、19.8），读取由 adapters 层实现。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal, Protocol

from .types import Contract


class InvalidTicker(Exception):
    """文本不是本系统接受的代码。→ Halt("invalid_ticker")"""


class UnknownTicker(Exception):
    """代码格式合法，但不在所选宇宙里。→ Halt("unknown_ticker")"""


class NoData(Exception):
    """该代码在给定日期（含）之前没有任何已结算的 bar。→ Halt("no_data")"""


class SourceUnavailable(Exception):
    """数据源读不出来，与代码本身无关。→ Halt("source_unavailable")（行情）或通道不可用（证据）"""


class NotCovered(Exception):
    """数据源没有这个标的，或覆盖不到这个日期/窗口。说明不了公司本身。→ 通道不可用，运行继续"""


class NotConfigured(Exception):
    """数据源需要的密钥没有配置（例如 IC_FRED_API_KEY）。→ 跳过该源，不算数据问题（BLUEPRINT 19.8）"""



@dataclass(frozen=True)
class Bar:
    """一根已结算的日 K 线。价格按数据源原样使用，不做复权。"""

    date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


class Instrument(Contract):
    """一个可分析标的的静态描述，由代码填写，不上模型的输出格式。"""

    ticker: str                   # 规范化代码，例如 "0700.HK"
    name: str
    market: Literal["US", "HK"]
    currency: str                 # 计价货币，只作标签，从不换算
    benchmark: str                # 对应的指数代码
    lot: int                      # 每手股数；未知为 0
    synthetic: bool               # True 表示合成数据（data/market/synthetic/）


class MarketData(Protocol):
    """行情端口。history 是读价格的唯一入口，必须带 as_of；没有返回某日之后数据的途径。"""

    def instrument(self, ticker: str) -> Instrument:
        """标的的静态描述。不在宇宙里抛 UnknownTicker。"""
        ...

    def history(self, ticker: str, as_of: datetime.date) -> tuple[Bar, ...]:
        """as_of（含）之前已结算的 bar，按日期升序。抛 NoData、SourceUnavailable。"""
        ...
