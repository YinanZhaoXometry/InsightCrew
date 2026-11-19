"""基本面的第二条计算路径（SQL）。只有核对（recompute_fundamentals）与测试使用。

与 domain/fundamentals.py 互不 import、不共享任何计算代码（tests/test_architecture.py 强制）。
选择规则同 18.6，全部在 SQL 里独立表达：最新财年取 period_end 的最大值，一个财年的取值
取其中 filed 的最大值，上一财年取与最新财年相差 330 到 400 天的 period_end 最大值
（julianday 计日差）。除以零在 SQLite 里得到 NULL，正好对应路径 1 的 None。
只用标准库（18.6）：年报行以结构匹配（_Row 协议）接收，不用任何 SQLite 数学扩展函数。
"""
from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Protocol


class _Row(Protocol):
    """一条年报行的形状；statements.Reported 结构匹配。只为类型检查存在（只读成员）。"""

    @property
    def concept(self) -> str: ...

    @property
    def period_end(self) -> datetime.date: ...

    @property
    def period_days(self) -> int: ...

    @property
    def value(self) -> float: ...

    @property
    def filed(self) -> datetime.date: ...

    @property
    def form(self) -> str: ...

# 一个 concept 最新财年的取值：period_end 最大，同财年取 filed 最大。
_LATEST = """
SELECT value FROM reported WHERE concept = :c AND filed = (
  SELECT max(filed) FROM reported WHERE concept = :c AND period_end = (
    SELECT max(period_end) FROM reported WHERE concept = :c))"""

# 同比：最新财年与上一财年（相差 330 到 400 天的 period_end 最大者）各取一次值。
_YOY = """
SELECT (cur.v - prev.v) / abs(prev.v) * 100 FROM
 (SELECT value AS v FROM reported WHERE concept = :c AND filed = (
    SELECT max(filed) FROM reported WHERE concept = :c AND period_end = (
      SELECT max(period_end) FROM reported WHERE concept = :c))) AS cur,
 (SELECT value AS v FROM reported WHERE concept = :c AND filed = (
    SELECT max(filed) FROM reported WHERE concept = :c AND period_end = (
      SELECT max(period_end) FROM reported WHERE concept = :c
        AND julianday((SELECT max(period_end) FROM reported WHERE concept = :c))
            - julianday(period_end) BETWEEN 330 AND 400))) AS prev"""

# 比值：两个 concept 在 concepts[0] 的最新财年上各取一次值。
_RATIO = """
SELECT a.v / b.v * 100 FROM
 (SELECT value AS v FROM reported WHERE concept = :a AND filed = (
    SELECT max(filed) FROM reported WHERE concept = :a AND period_end = (
      SELECT max(period_end) FROM reported WHERE concept = :a))) AS a,
 (SELECT value AS v FROM reported WHERE concept = :b AND filed = (
    SELECT max(filed) FROM reported WHERE concept = :b AND period_end = (
      SELECT max(period_end) FROM reported WHERE concept = :a))) AS b"""

# 市盈率：分析日收盘价除以最近财年每股收益；收益为零或负时无意义。
_MULTIPLE = """
SELECT CASE WHEN v.v IS NULL OR v.v <= 0 THEN NULL ELSE :close / v.v END FROM
 (SELECT value AS v FROM reported WHERE concept = :c AND filed = (
    SELECT max(filed) FROM reported WHERE concept = :c AND period_end = (
      SELECT max(period_end) FROM reported WHERE concept = :c))) AS v"""

# 申报间隔：最近财年最晚一次申报日到分析日的天数。
_AGE = """
SELECT cast(julianday(:as_of) - julianday(f.d) AS INTEGER) FROM
 (SELECT max(filed) AS d FROM reported WHERE concept = :c AND period_end = (
    SELECT max(period_end) FROM reported WHERE concept = :c)) AS f"""


def load(rows: Iterable[_Row]) -> sqlite3.Connection:
    """把年报行装进内存表。主键 (concept, period_end, filed)：端口保证不重复。"""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE reported (concept TEXT, period_end TEXT, period_days INTEGER, "
               "value REAL, filed TEXT, form TEXT, PRIMARY KEY (concept, period_end, filed))")
    db.executemany("INSERT INTO reported VALUES (?,?,?,?,?,?)",
                   [(r.concept, r.period_end.isoformat(), r.period_days, r.value,
                     r.filed.isoformat(), r.form) for r in rows])
    return db


def _one(db: sqlite3.Connection, sql: str, params: dict) -> float | None:
    row = db.execute(sql, params).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def latest(db: sqlite3.Connection, concepts: Sequence[str], *,
           close: float, as_of) -> float | None:
    return _one(db, _LATEST, {"c": concepts[0]})


def yoy_pct(db: sqlite3.Connection, concepts: Sequence[str], *,
            close: float, as_of) -> float | None:
    return _one(db, _YOY, {"c": concepts[0]})


def ratio_pct(db: sqlite3.Connection, concepts: Sequence[str], *,
              close: float, as_of) -> float | None:
    return _one(db, _RATIO, {"a": concepts[0], "b": concepts[1]})


def price_multiple(db: sqlite3.Connection, concepts: Sequence[str], *,
                   close: float, as_of) -> float | None:
    return _one(db, _MULTIPLE, {"c": concepts[0], "close": close})


def filing_age(db: sqlite3.Connection, concepts: Sequence[str], *,
               close: float, as_of) -> float | None:
    return _one(db, _AGE, {"c": concepts[0], "as_of": as_of.isoformat()})
