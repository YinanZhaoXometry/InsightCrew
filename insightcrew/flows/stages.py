"""代码阶段：把纯领域函数接到黑板上。

每个阶段做三件事：从黑板读输入、调用 domain 里的纯函数、把结果写回黑板。
阶段本身不含业务计算，也不调用模型。deps 由 app 层装配（见 BLUEPRINT 第 8.3 节）。

【待实现】工作包 WP5 实现以下五个函数。函数名、参数、写入的键不得改动：
analyze.py 和 tests/fake_stages.py 都依赖它们。
"""
from __future__ import annotations

from typing import Any

from ..domain.types import Finding
from ..kernel.runtime import Run


def intake(run: Run, request: dict[str, Any], deps: Any) -> None:
    """范围闸门。通过则写 request/scope；不在范围内则抛 Halt(原因)，此时不发生任何模型调用。"""
    raise NotImplementedError


def ledger(run: Run, deps: Any) -> None:
    """建立事实账本并用第二条路径复算。写 ledger/instrument、ledger/facts、ledger/card、rules/limits。"""
    raise NotImplementedError


def audit(run: Run, deps: Any) -> list[Finding]:
    """L2 数字核对 + L3 规则检查。每次调用都重写 audit/findings、audit/technical、audit/manager、
    audit/trader、audit/rules、audit/status，并记一条 gate.checked 事件。返回全部问题。"""
    raise NotImplementedError


def audited_current(run: Run) -> bool:
    """黑板上的当前读数、论点、方案，是否就是审计通过的那一版（比较 audit/status 里记录的序号）。"""
    raise NotImplementedError


def report(run: Run, deps: Any) -> None:
    """把黑板渲染成报告并写入运行目录。只读黑板，不做任何计算。"""
    raise NotImplementedError
