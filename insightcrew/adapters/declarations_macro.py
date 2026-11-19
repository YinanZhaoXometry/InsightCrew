"""macro.toml 的装载：把每张表校验成 domain/macro.py 的 MacroSpec（BLUEPRINT 19.7）。

与 adapters/declarations.py 的三个装载器同一纪律：表名就是 alias（表里再写 alias 是错误）；
缺字段、字段类型不对、出现契约之外的字段，都是启动期的错误，并且点名是哪张表的哪个字段。
TOML 的数组是 list，契约里的序列是 tuple；先做这一步表示转换，再按 strict 校验，
标量类型的笔误（例如 unit 写成数字）不会被悄悄转换。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from ..domain.macro import MacroSpec

# TOML 的数组是 list，契约里的序列是 tuple；strict 校验不接受 list，先做这一步表示转换。
_LIST_FIELDS = ("transforms",)


def load_macro(path: str | Path) -> tuple[MacroSpec, ...]:
    """读 macro.toml，每张表校验成一个 MacroSpec（表名是 alias），按文件顺序返回。"""
    with Path(path).open("rb") as f:
        tables = tomllib.load(f)
    return tuple(_spec(key, table, path) for key, table in tables.items())


def _spec(alias: str, table: object, path: str | Path) -> MacroSpec:
    """校验一张序列表。错误必须点名表和字段，让声明文件的笔误在启动时就被看见。"""
    if not isinstance(table, dict):
        raise ValueError(f"{path} 的 {alias!r} 不是一张宏观序列声明表")
    fields = dict(table)
    if "alias" in fields:
        raise ValueError(f"{path} 的表 {alias!r} 的字段 'alias' 不该在表里出现：表名就是 alias")
    for name in _LIST_FIELDS:
        if name not in fields:
            continue
        value = fields[name]
        if not isinstance(value, list):
            raise ValueError(f"{path} 的表 {alias!r} 的字段 {name!r} 应该是数组，"
                             f"实际是 {type(value).__name__}")
        fields[name] = tuple(value)
    try:
        return MacroSpec.model_validate({**fields, "alias": alias}, strict=True)
    except ValidationError as e:
        raise ValueError(f"{path} 的表 {alias!r} 无法校验为宏观序列声明：{e}") from e
