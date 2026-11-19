"""公告数据的契约与端口：公司自己发布的文件与公告（BLUEPRINT 18.5）。

条目是文本，不是数字：代码只核对它存在、它何时公开，读得对不对是判断，交给评审。
读取由 adapters 层实现（disclosures_json、eastmoney_ann、edgar）。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Published:
    """一条已公开的公司文件或公告。"""

    published: datetime.date
    kind: str                     # 类别，来源的说法，例如 "年度报告"、"8-K"
    title: str                    # 标题，来源原文，未经核验
    ref: str                      # 来源内部的编号，例如公告代码、受理号


@dataclass(frozen=True, slots=True)
class DisclosureSet:
    """一次读取得到的窗口内条目。"""

    source: str
    items: tuple[Published, ...]
    dropped: int                  # 无日期而被丢弃的行数


class Disclosures(Protocol):
    """公告端口。给一个代码和窗口 [start, as_of]，返回窗口内（含两端）公开的条目。

    抛 NotCovered（来源没有这个标的，或窗口超出其覆盖范围）、SourceUnavailable（读不出来）。
    """

    def published(self, ticker: str, start: datetime.date, as_of: datetime.date) -> DisclosureSet: ...
