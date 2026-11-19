"""Macro 端口的档案实现：读 data/market/<universe>/macro/<alias>.json（BLUEPRINT 19.8）。

一个文件只读一次并缓存。观测期（date）或版本（realtime_start）晚于分析日的观测
不存在（19.8：晚于分析日的观测绝不通过），没有任何方法拿到更晚的观测；文件里
带着将来才发布的版本（重述或尚未发布的月份）也没关系，读取时被这两条滤掉。

宏观档案没有 covers_from/covers_to：来源覆盖的范围就是文件里最早的观测期。比它更早
的分析日是 NotCovered（来源覆盖不到那个日期）；更晚的分析日正常读，只是拿到的观测
较早（宏观序列天然停在过去，这不是缺口）。分析日不早于最早的观测期、但届时一个版本
都还没发布时，返回观测为空的 Series（覆盖了、还没发布），由上层记为算不出，不是缺口。

观测行必须有日期与版本（date、realtime_start 都是 ISO 字符串）：无日期的观测行是坏
文件（SourceUnavailable），与无日期的年报行同判（18.5 规则 3）；宏观的 Series 契约里
没有 dropped 字段，所以没有「丢弃并计数」一说。文件缺失是 NotCovered；读不出来、字段
不合契约、文件自称的 alias 与文件名不一致、同一 (date, realtime_start) 出现两次，
都是 SourceUnavailable（文件坏了，而不是没有数据）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ..domain.macro import Observation, Series
from ..domain.market import NotCovered, SourceUnavailable


class _Obs(BaseModel):
    """JSON 里一条观测的映像：封闭契约，未知字段是错误。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str                  # ISO 日期：观测期；宏观观测必须有日期
    value: float
    realtime_start: str        # ISO 日期：这个版本开始生效的日子（FRED vintage）


class _File(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    alias: str                 # 必须与文件名（请求的别名）一致
    fred_id: str
    observations: tuple[_Obs, ...]


class JsonMacro:
    """Macro 端口的 JSON 实现，与 CsvMarket 同一布局：root/universe/macro/。"""

    def __init__(self, root: str | Path, universe: str):
        self._dir = Path(root) / universe / "macro"
        self._cache: dict[str, _File] = {}

    def series(self, alias: str, as_of: datetime.date) -> Series:
        """分析日（含）之前已发布、且观测期不晚于分析日的观测，保持文件顺序。"""
        file = self._load(alias)
        first = min(datetime.date.fromisoformat(o.date) for o in file.observations)
        if as_of < first:
            raise NotCovered(f"{alias} 的宏观数据覆盖从 {first.isoformat()} 起，不含 {as_of.isoformat()}")
        rows = [
            Observation(date=datetime.date.fromisoformat(o.date),
                        value=o.value,
                        realtime_start=datetime.date.fromisoformat(o.realtime_start))
            for o in file.observations
            if datetime.date.fromisoformat(o.date) <= as_of
            and datetime.date.fromisoformat(o.realtime_start) <= as_of]
        return Series(alias=file.alias, source=file.source, observations=tuple(rows))

    def _load(self, alias: str) -> _File:
        """读一个文件（只读一次）并校验成封闭契约。"""
        if alias in self._cache:
            return self._cache[alias]
        path = self._dir / f"{alias}.json"
        if not path.exists():
            raise NotCovered(f"在宇宙 {self._dir.parent.name} 里没有序列 {alias} 的宏观数据文件")
        try:
            file = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise SourceUnavailable(f"读不出 {path}：{e}") from e
        if file.alias != alias:
            raise SourceUnavailable(f"{path} 自称是 {file.alias!r}，与请求的 {alias!r} 不一致")
        if not file.observations:
            raise SourceUnavailable(f"{path} 里没有任何观测：文件坏了")
        seen: set[tuple[str, str]] = set()
        for o in file.observations:
            key = (o.date, o.realtime_start)
            if key in seen:
                raise SourceUnavailable(f"{path} 里 {key} 出现了两次：文件坏了")
            seen.add(key)
        self._cache[alias] = file
        return file
