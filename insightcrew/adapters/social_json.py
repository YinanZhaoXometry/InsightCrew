"""Social 端口的文件实现：读 data/market/<universe>/social/<file_stem>.json（BLUEPRINT 19.8）。

一个文件只读一次并缓存，之后按窗口过滤：发布日在 [start, as_of] 之内（两端含）的
帖子才返回；没有任何方法拿到更晚的帖子。无日期的行（published 为 null）不参与窗口，
丢弃并在 SocialSet.dropped 里计数。正文在构造 Post 时截到 280 字（端口契约的保证），
截断不算丢弃。文件缺失是 NotCovered；读不出来或字段不合契约是 SourceUnavailable。
窗口超出文件声明的覆盖范围（start 早于 covers_from，或 as_of 晚于 covers_to）也是
NotCovered：来源覆盖不到那个窗口（该窗口未覆盖），而不是窗口里没有帖子。
帖子按文件顺序返回；排序、截断与编号（S1 起）是 evidence 阶段的职责（19.12）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ..domain.market import NotCovered, SourceUnavailable
from ..domain.social import Post, SocialSet
from ..domain.symbols import file_stem

# 端口契约 Post.text 的上限（BLUEPRINT 19.7）。
_MAX_TEXT = 280


class _Post(BaseModel):
    """JSON 里一条帖子的映像：封闭契约，未知字段是错误。tag 合法值在读取时就校验，
    写成别的按坏文件处理（SourceUnavailable）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    published: str | None         # ISO 日期；null 表示这条没有日期，读取时丢弃并计数
    source: str
    text: str
    url: str
    tag: Literal["bullish", "bearish", "none"]   # 来源自带的标签；没有时为 none


class _File(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    covers_from: str              # ISO 日期：来源覆盖从哪天起
    covers_to: str                # ISO 日期：来源覆盖到哪天
    posts: tuple[_Post, ...]


class JsonSocial:
    """Social 端口的 JSON 实现，与 JsonDisclosures 同一布局：root/universe/social/。"""

    def __init__(self, root: str | Path, universe: str):
        self._dir = Path(root) / universe / "social"
        self._cache: dict[str, _File] = {}

    def posts(self, ticker: str, start: datetime.date,
              as_of: datetime.date) -> SocialSet:
        """窗口 [start, as_of]（两端含）内发布的帖子，保持文件顺序。"""
        file = self._load(ticker)
        covers_from = datetime.date.fromisoformat(file.covers_from)
        covers_to = datetime.date.fromisoformat(file.covers_to)
        if start < covers_from or as_of > covers_to:
            raise NotCovered(f"{ticker} 的社交数据覆盖 {file.covers_from} 到 {file.covers_to}，"
                             f"不含窗口 {start.isoformat()} 到 {as_of.isoformat()}")
        posts: list[Post] = []
        dropped = 0
        for row in file.posts:
            if row.published is None:
                dropped += 1
                continue
            day = datetime.date.fromisoformat(row.published)
            if start <= day <= as_of:
                posts.append(Post(published=day, source=row.source,
                                  text=row.text[:_MAX_TEXT], url=row.url, tag=row.tag))
        return SocialSet(source=file.source, posts=tuple(posts), dropped=dropped)

    def _load(self, ticker: str) -> _File:
        """读一个文件（只读一次）并校验成封闭契约。"""
        if ticker in self._cache:
            return self._cache[ticker]
        path = self._dir / f"{file_stem(ticker)}.json"
        if not path.exists():
            raise NotCovered(f"{ticker} 在宇宙 {self._dir.parent.name} 里没有社交数据文件")
        try:
            file = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError) as e:
            raise SourceUnavailable(f"读不出 {path}：{e}") from e
        self._cache[ticker] = file
        return file
