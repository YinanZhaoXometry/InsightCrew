# Adapted from TradingAgents (Apache-2.0), modified.
"""结算的第二条计算路径（SQL）。只有 flows/settle 的核对与测试使用。

与 domain/memory.py 互不 import、不共享任何计算代码；结算约定相同（19.13）：
入场价是分析日之后第一根收盘，持有期从入场那根算起第 holding_days 根收盘离场，
窗口没有在 known_by 之前走完时返回 None；基准在入场日与离场日各自按“当日或
之前最后一根收盘”估值。K 线装进内存表后全部在 SQL 里独立表达：ISO 日期字符串
的字典序即时间序；收盘非正的 K 线不装入（与路径 1 一致：那不是一根可结算的价）。

返回 SqlOutcome：与 OutcomeRecord 的计算字段一一对应。照抄字段（run_id、ticker、
as_of、benchmark 名、lesson）不经此路，由调用方从决策记录带过去，核对时只比
计算字段。只用标准库；决策与 K 线以结构匹配（_Decision、_Bar 协议）接收。
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Protocol


class _Decision(Protocol):
    """结算用到的决策字段；memory.DecisionRecord 结构匹配。只为类型检查存在。

    成员用只读 property：冻结契约的字段不可赋值，可变协议成员匹配不上。
    """

    @property
    def as_of(self) -> str: ...

    @property
    def action(self) -> str: ...

    @property
    def stop(self) -> float | None: ...

    @property
    def target(self) -> float | None: ...


class _Bar(Protocol):
    """一根 K 线的形状；market.Bar 结构匹配。只为类型检查存在（只读成员）。"""

    @property
    def date(self) -> datetime.date: ...

    @property
    def open(self) -> float: ...

    @property
    def high(self) -> float: ...

    @property
    def low(self) -> float: ...

    @property
    def close(self) -> float: ...

    @property
    def volume(self) -> float: ...


@dataclass(frozen=True)
class SqlOutcome:
    """路径 2 的结算结果：OutcomeRecord 的全部计算字段（照抄字段除外）。"""

    known_on: str
    holding_days: int
    return_pct: float
    benchmark_return_pct: float | None
    alpha_pct: float | None
    hit: str


# 窗口内（分析日之后、known_by 含）的 K 线根数，与指定位置那根的日期和收盘。
_COUNT = "SELECT count(*) FROM bars WHERE d > :as_of AND d <= :known_by"
_AT = ("SELECT d, c FROM bars WHERE d > :as_of AND d <= :known_by "
       "ORDER BY d LIMIT 1 OFFSET :n")

# 基准在某日当日或之前的最后一根收盘（两端的估值，与路径 1 的 asof 相同）。
_ASOF = "SELECT c FROM bench WHERE d <= :day ORDER BY d DESC LIMIT 1"

# 百分比与超额：与路径 1 同一的运算次序，双精度下两条路径逐位一致。
_RETURN = "SELECT (:exit_c - :entry_c) / :entry_c * 100.0"
_BENCH_RETURN = "SELECT (:b_exit - :b_entry) / :b_entry * 100.0"
_ALPHA = "SELECT :ret - :bench_ret"

# 持有窗口内（入场那根含、离场那根含）止损 / 目标第一次被触及的日期。
_STOP_DAY = ("SELECT min(d) FROM bars WHERE d > :as_of AND d <= :exit_day "
             "AND l <= :stop")
_TARGET_DAY = ("SELECT min(d) FROM bars WHERE d > :as_of AND d <= :exit_day "
               "AND h >= :target")


def settle(decision: _Decision, bars: tuple[_Bar, ...], benchmark_bars: tuple[_Bar, ...],
           holding_days: int, known_by: datetime.date) -> SqlOutcome | None:
    """结算一条决策（路径 2）：约定与 memory.settle 相同，全部在 SQL 里表达。"""
    if holding_days < 1:
        raise ValueError(f"持有期至少要一个交易日，得到 {holding_days}")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bars (d TEXT, o REAL, h REAL, l REAL, c REAL, v REAL)")
    db.execute("CREATE TABLE bench (d TEXT, o REAL, h REAL, l REAL, c REAL, v REAL)")
    _fill(db, "bars", bars)
    _fill(db, "bench", benchmark_bars)
    window: dict[str, object] = {"as_of": decision.as_of, "known_by": known_by.isoformat()}
    counted = db.execute(_COUNT, window).fetchone()
    if counted is None or int(counted[0]) <= holding_days:
        return None
    first = db.execute(_AT, window | {"n": 0}).fetchone()
    last = db.execute(_AT, window | {"n": holding_days}).fetchone()
    if first is None or last is None:
        return None
    entry_day, entry_close = str(first[0]), float(first[1])
    exit_day, exit_close = str(last[0]), float(last[1])
    return_pct = _number(db, _RETURN, {"entry_c": entry_close, "exit_c": exit_close})
    if return_pct is None:
        return None    # 两根收盘都已取出，这只挡类型收窄
    b_entry = _number(db, _ASOF, {"day": entry_day})
    b_exit = _number(db, _ASOF, {"day": exit_day})
    benchmark_return_pct: float | None = None
    alpha_pct: float | None = None
    if b_entry is not None and b_exit is not None:
        benchmark_return_pct = _number(db, _BENCH_RETURN, {"b_entry": b_entry, "b_exit": b_exit})
        if benchmark_return_pct is not None:
            alpha_pct = _number(db, _ALPHA, {"ret": return_pct, "bench_ret": benchmark_return_pct})
    return SqlOutcome(known_on=exit_day, holding_days=holding_days, return_pct=return_pct,
                      benchmark_return_pct=benchmark_return_pct, alpha_pct=alpha_pct,
                      hit=_hit(db, decision, exit_day))


def _hit(db: sqlite3.Connection, decision: _Decision, exit_day: str) -> str:
    """谁先被触及：两次 min(d) 比较，同一根都触及按止损算（与路径 1 一致）。"""
    if decision.action == "Hold":
        return "n/a"
    span: dict[str, object] = {"as_of": decision.as_of, "exit_day": exit_day}
    stop_day = (_text(db, _STOP_DAY, span | {"stop": decision.stop})
                if decision.stop is not None else None)
    target_day = (_text(db, _TARGET_DAY, span | {"target": decision.target})
                  if decision.target is not None else None)
    if stop_day is not None and (target_day is None or stop_day <= target_day):
        return "stop"
    if target_day is not None:
        return "target"
    return "neither"


def _fill(db: sqlite3.Connection, table: str, bars: tuple[_Bar, ...]) -> None:
    """把 K 线装进内存表；收盘非正的不装（那不是一根可结算的价格）。表名是本模块写死的。"""
    db.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?)",
                   [(b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume)
                    for b in bars if b.close > 0])


def _number(db: sqlite3.Connection, sql: str, params: dict[str, object]) -> float | None:
    row = db.execute(sql, params).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def _text(db: sqlite3.Connection, sql: str, params: dict[str, object]) -> str | None:
    row = db.execute(sql, params).fetchone()
    return None if row is None or row[0] is None else str(row[0])
