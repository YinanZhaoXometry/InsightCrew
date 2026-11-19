"""工具指标声明的装载：把 tool_indicators.toml 校验成 ToolSpec（蓝图 19.11）。

与 declarations.py 同一风格：用 tomllib 读文件，每张表校验成它的契约，表名作为 key；
缺字段、字段类型不对、出现契约之外的字段，都是启动期的错误，
并且点名是哪张表的哪个字段。ToolSpec 是 strict 模型：标量类型不对
（例如字符串的窗口下限）不会被悄悄转换。表的顺序就是文件的顺序。
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, ValidationError, model_validator

from ..domain.types import Contract


class ToolSpec(Contract):
    """一个工具家族的声明：tool_indicators.toml 里一张表的映像（蓝图 19.11）。

    不上黑板，不进模型输出格式；flows/tools.py 按结构（而非类型）接收它。
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    key: str                      # 表名，即工具调用里的 name，例如 "sma"
    fn: str                       # 两条路径里同名的函数，例如 "sma"
    inputs: tuple[str, ...]       # 输入列，取值 open/high/low/close/volume
    arg: str                      # 窗口参数名；蓝图 19.11 的七个家族都是 "n"
    min: int                      # 窗口参数允许的最小值（含）
    max: int                      # 窗口参数允许的最大值（含）
    unit: Literal["price", "pct", "shares", "none"]   # price 换成计价货币；pct 为 "%"；shares 原样；none 为 ""
    label: str                    # 中文名，含 {arg} 占位符，返回时替换成实际窗口

    @model_validator(mode="after")
    def _range_and_placeholder(self) -> ToolSpec:
        """范围必须可用（1 ≤ min ≤ max），label 必须带窗口占位符：笔误在启动时就被看见。"""
        if self.min < 1 or self.max < self.min:
            raise ValueError(f"min={self.min} 与 max={self.max} 构不成一个可用的窗口范围")
        if "{" + self.arg + "}" not in self.label:
            raise ValueError(f"label {self.label!r} 缺少占位符 {{{self.arg}}}")
        return self


def load_tool_indicators(path: str | Path) -> tuple[ToolSpec, ...]:
    """读 tool_indicators.toml，每张表校验成一个 ToolSpec（表名是 key），按文件顺序返回。"""
    with Path(path).open("rb") as f:
        tables = tomllib.load(f)
    return tuple(_tool(key, table, path) for key, table in tables.items())


def _tool(key: str, table: object, path: str | Path) -> ToolSpec:
    """校验一张表。错误必须点名表和字段，让声明文件的笔误在启动时就被看见。"""
    if not isinstance(table, dict):
        raise ValueError(f"{path} 的 {key!r} 不是一张工具指标声明表")
    fields = dict(table)
    if "key" in fields:
        raise ValueError(f"{path} 的表 {key!r} 的字段 'key' 不该在表里出现：表名就是 key")
    if "inputs" in fields:
        value = fields["inputs"]
        if not isinstance(value, list):
            raise ValueError(f"{path} 的表 {key!r} 的字段 'inputs' 应该是数组，"
                             f"实际是 {type(value).__name__}")
        fields["inputs"] = tuple(value)
    try:
        return ToolSpec.model_validate({**fields, "key": key})
    except ValidationError as e:
        raise ValueError(f"{path} 的表 {key!r} 无法校验为工具指标声明：{e}") from e
