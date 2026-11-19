"""Disclosures 端口的文件实现：读 data/market/<universe>/disclosures/<file_stem>.json。

一个文件只读一次并缓存，之后按窗口过滤：发布日在 [start, as_of] 之内（两端含）的
条目才返回（18.5 规则 4：分析日当天发布的可见）；没有任何方法拿到更晚的条目。
无日期的行（published 为 null）不参与窗口，丢弃并在 DisclosureSet.dropped 里计数
（18.5 规则 3）。文件缺失是 NotCovered；读不出来或字段不合契约是 SourceUnavailable。
窗口超出文件声明的覆盖范围（start 早于 covers_from，或 as_of 晚于 covers_to）也是
NotCovered：来源覆盖不到那个窗口，而不是窗口里没有公告（18.5 规则 5）。
条目按文件顺序返回；排序、截断与编号（D1 起）是 evidence 阶段的职责（18.8）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ..domain.disclosures import DisclosureSet, Published
from ..domain.market import NotCovered, SourceUnavailable
from ..domain.symbols import file_stem


class _Item(BaseModel):
    """JSON 里一条公告的映像：封闭契约，未知字段是错误。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    published: str | None       # ISO 日期；null 表示这条没有日期，读取时丢弃并计数
    kind: str
    title: str
    ref: str


class _File(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    covers_from: str            # ISO 日期：来源覆盖从哪天起
    covers_to: str              # ISO 日期：来源覆盖到哪天
    items: tuple[_Item, ...]


class JsonDisclosures:
    """Disclosures 端口的 JSON 实现，与 CsvMarket 同一布局：root/universe/disclosures/。"""

    def __init__(self, root: str | Path, universe: str):
        self._dir = Path(root) / universe / "disclosures"
        self._cache: dict[str, _File] = {}

    def published(self, ticker: str, start: datetime.date,
                  as_of: datetime.date) -> DisclosureSet:
        """窗口 [start, as_of]（两端含）内公开的条目，保持文件顺序。"""
        file = self._load(ticker)
        covers_from = datetime.date.fromisoformat(file.covers_from)
        covers_to = datetime.date.fromisoformat(file.covers_to)
        if start < covers_from or as_of > covers_to:
            raise NotCovered(f"{ticker} 的公告数据覆盖 {file.covers_from} 到 {file.covers_to}，"
                             f"不含窗口 {start.isoformat()} 到 {as_of.isoformat()}")
        items: list[Published] = []
        dropped = 0
        for item in file.items:
            if item.published is None:
                dropped += 1
                continue
            day = datetime.date.fromisoformat(item.published)
            if start <= day <= as_of:
                items.append(Published(published=day, kind=item.kind,
                                       title=item.title, ref=item.ref))
        return DisclosureSet(source=file.source, items=tuple(items), dropped=dropped)

    def _load(self, ticker: str) -> _File:
        """读一个文件（只读一次）并校验成封闭契约。"""
        if ticker in self._cache:
            return self._cache[ticker]
        path = self._dir / f"{file_stem(ticker)}.json"
        if not path.exists():
            raise NotCovered(f"{ticker} 在宇宙 {self._dir.parent.name} 里没有公告数据文件")
        try:
            file = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise SourceUnavailable(f"读不出 {path}：{e}") from e
        self._cache[ticker] = file
        return file
