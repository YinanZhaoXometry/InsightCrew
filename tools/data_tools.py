"""数据工具：schema 探查 + SQL 查询。

这里藏着一个重要的设计决策：
    **确定性的事情不用模型做。**
    字段类型、缺失率、行数、求和——这些都是确定性计算，
    交给 SQL/Python 100% 准确；模型只负责"决定算什么"。
    这是把 LLM 的不确定性压缩到最小面的关键手法。

l2_value() 是审计者专用的"第二条计算路径"：
    Analyst 在沙箱里用 Python/csv 算，Critic 在这里用 SQLite 算。
    两条路径独立实现，结果不一致 = 算术错。
"""
from __future__ import annotations

import csv
import os
import sqlite3

from config import DATA_DIR

_DB = None


def table_names() -> list[str]:
    return sorted(f[:-4] for f in os.listdir(DATA_DIR) if f.endswith(".csv"))


def data_files() -> list[str]:
    return [os.path.join(DATA_DIR, f + ".csv") for f in table_names()]


def _connect() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        _DB = sqlite3.connect(":memory:")
        for t in table_names():
            path = os.path.join(DATA_DIR, t + ".csv")
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if not rows:
                continue
            header = rows[0]
            cols = []
            for c in header:
                cols.append(f'"{c}" TEXT')      # 统一按 TEXT 存，数值靠 CAST
            _DB.execute(f'CREATE TABLE "{t}" ({", ".join(cols)})')
            ph = ",".join("?" * len(header))
            _DB.executemany(f'INSERT INTO "{t}" VALUES ({ph})', rows[1:])
            _DB.commit()
    return _DB


def sql(query: str, params: tuple = ()) -> list[tuple]:
    return _connect().execute(query, params).fetchall()


def schema_text() -> str:
    """给模型看的表结构说明。包含缺失率与数值范围——这两项最能暴露数据坑。"""
    lines = []
    for t in table_names():
        path = os.path.join(DATA_DIR, t + ".csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        header, body = rows[0], rows[1:]
        lines.append(f"表 {t}：{len(body)} 行，字段 {header}")
        for i, col in enumerate(header):
            vals = [r[i] for r in body if i < len(r)]
            empties = sum(1 for v in vals if v == "")
            nums = []
            for v in vals:
                try:
                    nums.append(float(v))
                except ValueError:
                    pass
            extra = ""
            if nums and len(nums) > len(vals) * 0.8:
                extra = f"，数值范围 [{min(nums):,.2f}, {max(nums):,.2f}]"
            if empties:
                extra += f"，空值 {empties} 个"
            lines.append(f"  - {col}: {vals[0] if vals else ''} 等{extra}")
        lines.append("")
    return "\n".join(lines)


def l2_value(filters: dict, metric: str = "营收") -> float | None:
    """审计者的独立复算路径（SQL）。

    刻意只依赖 filters 与指标名这两个结构化输入，不读 Analyst 的代码——
    这样它才算"独立实现"，而不是"照着原代码再跑一遍"。
    具体公式在 tools/metrics.py，按指标名派发；未知指标返回 None。
    """
    from tools import metrics          # 延迟导入，避免与 metrics -> data_tools 循环

    return metrics.compute(metric, filters)
