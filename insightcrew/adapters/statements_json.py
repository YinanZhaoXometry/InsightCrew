"""Statements 端口的文件实现：读 data/market/<universe>/statements/<file_stem>.json。

一个文件只读一次并缓存，之后按 分析日 过滤：申报日（filed）晚于分析日的行不存在
（18.5 规则 1、2），没有任何方法拿到更晚的行。文件缺失是 NotCovered（这个宇宙没有
这家公司的年报数据）；读不出来、字段不合契约、行缺申报日、同一 (concept, period_end,
filed) 出现两次，都是 SourceUnavailable（文件坏了，而不是没有数据）。
分析日晚于文件声明的 covers_to 也是 NotCovered：来源覆盖不到那个日期（18.5 规则 5）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ..domain.market import NotCovered, SourceUnavailable
from ..domain.statements import Reported, StatementSet
from ..domain.symbols import file_stem


class _Row(BaseModel):
    """JSON 里一行的映像：封闭契约，未知字段是错误。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str
    period_end: str            # ISO 日期
    period_days: int
    value: float
    filed: str                 # ISO 日期；年报行必须有申报日（18.5 规则 3）
    form: str


class _File(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    currency: str
    covers_to: str             # ISO 日期：来源覆盖到哪天
    rows: tuple[_Row, ...]


class JsonStatements:
    """Statements 端口的 JSON 实现，与 CsvMarket 同一布局：root/universe/statements/。"""

    def __init__(self, root: str | Path, universe: str):
        self._dir = Path(root) / universe / "statements"
        self._cache: dict[str, _File] = {}

    def statements(self, ticker: str, as_of: datetime.date) -> StatementSet:
        """分析日（含）之前已公开的年度报表行，保持文件顺序。"""
        file = self._load(ticker)
        if as_of > datetime.date.fromisoformat(file.covers_to):
            raise NotCovered(f"{ticker} 的年报数据覆盖到 {file.covers_to}，不含 {as_of.isoformat()}")
        rows = tuple(
            Reported(concept=row.concept, period_end=datetime.date.fromisoformat(row.period_end),
                     period_days=row.period_days, value=row.value,
                     filed=datetime.date.fromisoformat(row.filed), form=row.form)
            for row in file.rows
            if datetime.date.fromisoformat(row.filed) <= as_of)
        return StatementSet(source=file.source, currency=file.currency, rows=rows)

    def _load(self, ticker: str) -> _File:
        """读一个文件（只读一次）并校验成封闭契约。"""
        if ticker in self._cache:
            return self._cache[ticker]
        path = self._dir / f"{file_stem(ticker)}.json"
        if not path.exists():
            raise NotCovered(f"{ticker} 在宇宙 {self._dir.parent.name} 里没有年报数据文件")
        try:
            file = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise SourceUnavailable(f"读不出 {path}：{e}") from e
        seen: set[tuple[str, str, str]] = set()
        for row in file.rows:
            key = (row.concept, row.period_end, row.filed)
            if key in seen:
                raise SourceUnavailable(f"{path} 里 {key} 出现了两次：文件坏了")
            seen.add(key)
        self._cache[ticker] = file
        return file
