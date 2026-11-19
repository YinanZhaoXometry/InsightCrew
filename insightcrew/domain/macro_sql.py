"""宏观的第二条计算路径（SQL）。只有核对与测试使用（BLUEPRINT 19.7、19.8）。

与 domain/macro.py 互不 import、不共享任何计算代码（tests/test_macro.py 强制）。
时点规则全部在 SQL 里独立表达：入选条件（观测期 date 不晚于月末边界、版本
realtime_start 不晚于分析日）与选取次序（观测期最晚优先，同观测期取 realtime_start
最晚的版本）写成 ORDER BY … DESC LIMIT 1；月末边界与「往前 k 个月」的月末用 SQLite
自己的日期修饰符从参数日期推出来（'start of month' 归到当月一号，再加减月份后
加一个月减一天），不用任何扩展函数。与路径 1 相同：缺所需观测时查不到行或得到
NULL（这里都转成 None），绝不拿 0 顶替。
只用标准库：观测以结构匹配（_Observation 协议）接收，不 import macro。
"""
from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterable
from typing import Protocol


class _Observation(Protocol):
    """一条观测的形状；macro.Observation 结构匹配。只为类型检查存在（只读成员）。"""

    @property
    def date(self) -> datetime.date: ...

    @property
    def value(self) -> float: ...

    @property
    def realtime_start(self) -> datetime.date: ...


# 月末边界：参数日期归到当月一号，加一个月再减一天，即该月最后一天。
_END = "date(:m, 'start of month', '+1 month', '-1 day')"
_END_3M = "date(:m, 'start of month', '-3 months', '+1 month', '-1 day')"
_END_12M = "date(:m, 'start of month', '-12 months', '+1 month', '-1 day')"


def _pick(end: str) -> str:
    """一个序列在 end 那个月末、截至 :as_of 的值：两条入选条件加上选取次序。"""
    return (f"SELECT value FROM obs WHERE date <= {end} AND realtime_start <= :as_of "
            "ORDER BY date DESC, realtime_start DESC LIMIT 1")


# 水平值。
_LEVEL = _pick(_END)

# 3 个月变化与同比：两个月末各取一次值再相减；任一头查不到行，联结即为空 → NULL。
_CHG_3M = f"""
SELECT a.value - b.value FROM
 ({_pick(_END)}) AS a,
 ({_pick(_END_3M)}) AS b"""

_YOY = f"""
SELECT a.value - b.value FROM
 ({_pick(_END)}) AS a,
 ({_pick(_END_12M)}) AS b"""


def load(observations: Iterable[_Observation]) -> sqlite3.Connection:
    """把观测装进内存表。主键 (date, realtime_start)：档案保证同一观测期同一版本只出现一次。"""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE obs (date TEXT, value REAL, realtime_start TEXT, "
               "PRIMARY KEY (date, realtime_start))")
    db.executemany("INSERT INTO obs VALUES (?,?,?)",
                   [(o.date.isoformat(), o.value, o.realtime_start.isoformat())
                    for o in observations])
    return db


def _one(db: sqlite3.Connection, sql: str, month_end: datetime.date,
         as_of: datetime.date) -> float | None:
    row = db.execute(sql, {"m": month_end.isoformat(),
                           "as_of": as_of.isoformat()}).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def level(db: sqlite3.Connection, *, month_end: datetime.date,
          as_of: datetime.date) -> float | None:
    return _one(db, _LEVEL, month_end, as_of)


def chg_3m(db: sqlite3.Connection, *, month_end: datetime.date,
           as_of: datetime.date) -> float | None:
    return _one(db, _CHG_3M, month_end, as_of)


def yoy(db: sqlite3.Connection, *, month_end: datetime.date,
        as_of: datetime.date) -> float | None:
    return _one(db, _YOY, month_end, as_of)
