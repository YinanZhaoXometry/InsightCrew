"""声明文件的装载：把 indicators.toml 校验成领域里的 IndicatorSpec。

蓝图 9.5：用 tomllib 读文件，每张表校验成它的契约，表名作为 key；
缺字段、字段类型不对、出现契约之外的字段，都是启动期的错误，
并且点名是哪张表的哪个字段。load_rules（rules.toml → RuleSpec）在 WP4B 加入。
IndicatorSpec 是 strict 模型：标量类型不对（例如字符串的根数）不会被悄悄转换。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from ..domain.ledger import IndicatorSpec

# TOML 的数组是 list，契约里的序列是 tuple；strict 校验不接受 list，先做这一步表示转换。
_LIST_FIELDS = ("inputs", "labels", "outputs")


def load_indicators(path: str | Path) -> tuple[IndicatorSpec, ...]:
    """读 indicators.toml，每张表校验成一个 IndicatorSpec（表名是 key），按文件顺序返回。"""
    with Path(path).open("rb") as f:
        tables = tomllib.load(f)
    return tuple(_indicator(key, table, path) for key, table in tables.items())


def _indicator(key: str, table: object, path: str | Path) -> IndicatorSpec:
    """校验一张表。错误必须点名表和字段，让声明文件的笔误在启动时就被看见。"""
    if not isinstance(table, dict):
        raise ValueError(f"{path} 的 {key!r} 不是一张指标声明表")
    fields = dict(table)
    if "key" in fields:
        raise ValueError(f"{path} 的表 {key!r} 的字段 'key' 不该在表里出现：表名就是 key")
    for name in _LIST_FIELDS:
        if name not in fields:
            continue
        value = fields[name]
        if not isinstance(value, list):
            raise ValueError(f"{path} 的表 {key!r} 的字段 {name!r} 应该是数组，实际是 {type(value).__name__}")
        fields[name] = tuple(value)
    fields.setdefault("outputs", (key,))          # 单输出省略 outputs：唯一输出就是表名
    try:
        return IndicatorSpec.model_validate({**fields, "key": key})
    except ValidationError as e:
        raise ValueError(f"{path} 的表 {key!r} 无法校验为指标声明：{e}") from e
