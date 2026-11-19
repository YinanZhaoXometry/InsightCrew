# Adapted from TradingAgents (Apache-2.0), modified.
"""记忆的契约与结算的第一条计算路径（Python）。BLUEPRINT 19.7、19.13。

契约逐字取自 19.7：DecisionRecord（一次已交付的决策）、OutcomeRecord（代码算出的
结果）、Lesson（复盘员的输出）、LessonEntry（板键 memory/lessons 的元素）。

结算约定（改写自 TradingAgents memory/settlement.py，重写为本项目的契约）：
- 入场价 = 分析日之后第一根 K 线的收盘价；持有期从入场那根算起，第 holding_days
  根收盘离场；known_on = 离场那根 K 线的日期，结果自此可知。
- 持有窗口没有在 known_by 之前走完时返回 None：绝不拿部分窗口结账，留待下次再试。
- return_pct = 离场收盘对入场收盘的涨跌幅（百分比）；与决策的动作无关，Hold 也
  照算——量的是这段行情本身，不是某笔持仓的盈亏。
- 基准在入场日与离场日各自按“当日或之前最后一根收盘”估值；任一端无价则
  benchmark_return_pct 与 alpha_pct 为 null，结果本身仍然成立。
- hit：持有窗口（入场到离场，含两端）内止损与目标谁先被触及；同一根都触及时按
  止损算（从保守）；Hold 没有价位，记 n/a。

第二条路径在 domain/memory_sql.py（SQL）：两路互不 import、不共享任何计算代码，
每一条结果必须一致。select_lessons 只交出 known_on 不晚于分析日的教训（不变量 20）。
"""
from __future__ import annotations

import datetime
from typing import Literal

from pydantic import Field

from .market import Bar
from .types import Action, Contract, Finding, Rating


class DecisionRecord(Contract):
    """一次已交付的决策：决策日志里的一条 decision 记录。"""

    kind: Literal["decision"]
    run_id: str
    ticker: str
    as_of: str
    rating: Rating
    action: Action
    entry: float | None
    stop: float | None
    target: float | None
    size_pct: float
    horizon_days: int


class OutcomeRecord(Contract):
    """一条决策的结算结果：决策日志里的一条 outcome 记录，由代码算出。"""

    kind: Literal["outcome"]
    run_id: str
    ticker: str
    as_of: str
    known_on: str                          # 持有期最后一根 K 线的日期；结果自此可知
    holding_days: int
    return_pct: float                      # 分析日后第一根收盘到持有期末收盘
    benchmark: str                         # 基准的文件名词干，例如 "SPY"、"_HSI"
    benchmark_return_pct: float | None
    alpha_pct: float | None
    hit: Literal["stop", "target", "neither", "n/a"]   # 持有期内先触及谁；Hold 为 n/a
    lesson: str | None                     # reflector 写；未写时为 null


class Lesson(Contract):
    """复盘员（reflector）的输出：一条已结算决策的可迁移经验。"""

    text: str = Field(description="一条可迁移的经验，一到两句，不要包含数字")
    cites: tuple[Literal["return_pct", "alpha_pct", "hit", "rating", "action"], ...]


class LessonEntry(Contract):
    """板键 memory/lessons 的元素：一条教训连同它依据的数字。"""

    ticker: str
    as_of: str
    known_on: str
    rating: str
    return_pct: float
    alpha_pct: float | None
    hit: str
    lesson: str


def benchmark_for(ticker: str) -> str:
    """该代码对应的基准文件名词干：港股为 _HSI，其余为 SPY（BLUEPRINT 19.7）。"""
    return "_HSI" if ticker.upper().endswith(".HK") else "SPY"


def settle(decision: DecisionRecord, bars: tuple[Bar, ...], benchmark_bars: tuple[Bar, ...],
           holding_days: int, known_by: datetime.date) -> OutcomeRecord | None:
    """结算一条决策：持有窗口已在 known_by 之前走完时给出结果，否则 None。

    只用分析日之后、known_by（含）之前的 K 线；收盘非正的 K 线不算一根可结算的
    价格。holding_days 由调用方给（[memory] 的持有期设置），不用决策自己写的
    horizon_days——结算窗口是记忆的口径，不是那次决策的口径。
    """
    if holding_days < 1:
        raise ValueError(f"持有期至少要一个交易日，得到 {holding_days}")
    as_of = datetime.date.fromisoformat(decision.as_of)
    window = [b for b in bars if as_of < b.date <= known_by and b.close > 0]
    if len(window) <= holding_days:
        return None
    entry, exit_ = window[0], window[holding_days]
    return_pct = (exit_.close - entry.close) / entry.close * 100
    bench = [b for b in benchmark_bars if b.close > 0]
    b_entry = _close_by(bench, entry.date)
    b_exit = _close_by(bench, exit_.date)
    if b_entry is None or b_exit is None:
        benchmark_return_pct = alpha_pct = None
    else:
        benchmark_return_pct = (b_exit - b_entry) / b_entry * 100
        alpha_pct = return_pct - benchmark_return_pct
    return OutcomeRecord(
        kind="outcome", run_id=decision.run_id, ticker=decision.ticker, as_of=decision.as_of,
        known_on=exit_.date.isoformat(), holding_days=holding_days, return_pct=return_pct,
        benchmark=benchmark_for(decision.ticker), benchmark_return_pct=benchmark_return_pct,
        alpha_pct=alpha_pct, hit=_hit(decision, window[:holding_days + 1]), lesson=None)


def select_lessons(records: list[DecisionRecord | OutcomeRecord], ticker: str,
                   as_of: datetime.date, same: int = 5,
                   other: int = 3) -> tuple[LessonEntry, ...]:
    """从日志记录里挑给经理看的教训（BLUEPRINT 19.13）。

    只取已写教训、且 known_on 不晚于 as_of 的结果（不变量 20：绝不学到还没发生
    的结果）；评级不在结果里，回到同一 (as_of, ticker) 的决策记录去取，配不上
    决策的结果跳过。先给本代码最新的 same 条，再给其他代码最新的 other 条；
    “最新”按决策日，同日按写入顺序（后写的算新）。
    """
    ratings = {(r.as_of, r.ticker): r.rating
               for r in records if isinstance(r, DecisionRecord)}
    known: list[tuple[int, OutcomeRecord, str]] = []
    for i, r in enumerate(records):
        if (isinstance(r, OutcomeRecord) and r.lesson is not None
                and (r.as_of, r.ticker) in ratings
                and datetime.date.fromisoformat(r.known_on) <= as_of):
            known.append((i, r, r.lesson))
    known.sort(key=lambda t: (t[1].as_of, t[0]), reverse=True)
    newest = [t for t in known if t[1].ticker == ticker][:same]
    cross = [t for t in known if t[1].ticker != ticker][:other]
    return tuple(
        LessonEntry(ticker=r.ticker, as_of=r.as_of, known_on=r.known_on,
                    rating=str(ratings[(r.as_of, r.ticker)]), return_pct=r.return_pct,
                    alpha_pct=r.alpha_pct, hit=r.hit, lesson=lesson)
        for _, r, lesson in (*newest, *cross))


def lesson_findings(outcome: OutcomeRecord, lesson: Lesson) -> list[Finding]:
    """复盘输出的审计（BLUEPRINT 19.13）：L2 核对引用与文字，owner 都是 reflector。"""
    out: list[Finding] = []
    if not lesson.cites:
        out.append(Finding(check="lesson_cites_outcome", layer="L2", owner="reflector",
                           subject="lesson.cites",
                           message="教训没有引用任何结果字段，至少要引用一个"))
    if "alpha_pct" in lesson.cites and outcome.alpha_pct is None:
        out.append(Finding(check="lesson_cites_known_field", layer="L2", owner="reflector",
                           subject="lesson.cites",
                           message="教训引用了 alpha_pct，但这次结算没有基准价，超额收益不存在"))
    if any(ch.isdigit() for ch in lesson.text):
        out.append(Finding(check="lesson_text_has_no_numbers", layer="L2", owner="reflector",
                           subject="lesson.text",
                           message="教训文字里出现了数字；请改用文字表述，数字只留在结果记录里"))
    return out


def _close_by(bars: list[Bar], day: datetime.date) -> float | None:
    """基准在 day 当日或之前最后一根收盘价；没有则为 None，绝不拿更晚的价。"""
    closes = [b.close for b in bars if b.date <= day]
    return closes[-1] if closes else None


def _hit(decision: DecisionRecord,
         window: list[Bar]) -> Literal["stop", "target", "neither", "n/a"]:
    """持有窗口（含入场与离场两根）内止损与目标谁先被触及；Hold 没有价位，记 n/a。"""
    if decision.action is Action.HOLD:
        return "n/a"
    stop_day = next((b.date for b in window
                     if decision.stop is not None and b.low <= decision.stop), None)
    target_day = next((b.date for b in window
                       if decision.target is not None and b.high >= decision.target), None)
    if stop_day is not None and (target_day is None or stop_day <= target_day):
        return "stop"          # 同一根都触及也按止损算：从保守
    if target_day is not None:
        return "target"
    return "neither"
