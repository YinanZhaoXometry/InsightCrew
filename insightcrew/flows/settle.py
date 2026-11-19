"""结算（BLUEPRINT 19.13）：把持有期已走完的待决决策算成结果，两条路径一致才落账。

对每个新结果开一个小运行（runs/memory/settle/<id>/）：复盘员（reflector）读
settle/decision 与 settle/outcome 写一条教训，lesson_findings 审计通过才把教训
随结果一起写回决策日志；复盘员不可用或审计不通过时结果照常落账，教训留空，
下次结算不会重来（同一条决策只结一次，日志的 resolve 是一次性的）。

flows 不 import app：模型来源、运行根目录、预算上限由命令层（app/settle.py、
app/backtest.py）装配后经关键字参数传入；不给模型时跳过复盘，只结算。
"""
from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..domain import memory_sql
from ..domain.market import Bar, NoData, SourceUnavailable, UnknownTicker
from ..domain.memory import DecisionRecord, OutcomeRecord, benchmark_for, lesson_findings
from ..domain.memory import settle as settle_path_one
from ..domain.symbols import file_stem
from ..flows.deps import Deps
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Journal
from ..kernel.runtime import Halt, Limits, Malformed, Models, OutputMode, Run, run_role
from ..roles import REFLECTOR

Note = Callable[[str], None]          # 结算过程的一行汇报（命令层传 print）

# 两条路径逐项核对的六个计算字段（照抄字段不经路径 2，HANDOFF 问题 24 的接口）。
_FIELDS = ("known_on", "holding_days", "return_pct", "benchmark_return_pct", "alpha_pct", "hit")


async def settle(deps: Deps, log: Any, known_by: date, *, holding_days: int = 5,
                 models: Models | None = None, runs_root: Path | None = None,
                 limits: Limits | None = None, output_mode: OutputMode = "auto",
                 note: Note | None = None) -> list[OutcomeRecord]:
    """结算每个持有期已在 known_by 之前走完的待决决策，返回新落账的结果。

    log 是 DecisionLog（写入）或同形的替身；两条路径（memory 与 memory_sql）对六个
    计算字段逐项一致才落账，不一致或行情读不出的决策留在 pending 并经 note 汇报。
    持有期与复盘运行的关键字参数由命令层装配传入（flows 不 import app）。
    """
    say = note or (lambda _line: None)
    records = log.records()
    settled_keys = {(r.as_of, r.ticker) for r in records if isinstance(r, OutcomeRecord)}
    pending = [r for r in records
               if isinstance(r, DecisionRecord) and (r.as_of, r.ticker) not in settled_keys]
    outcomes: list[OutcomeRecord] = []
    for decision in pending:
        bars = _stock_bars(deps, decision.ticker, known_by, say)
        if bars is None:
            continue
        bench = _bench_bars(deps, benchmark_for(decision.ticker), known_by)
        one = settle_path_one(decision, bars, bench, holding_days, known_by)
        two = memory_sql.settle(decision, bars, bench, holding_days, known_by)
        if one is None and two is None:
            continue                              # 持有期还没走完，留待下次
        if one is None or two is None or any(getattr(one, f) != getattr(two, f) for f in _FIELDS):
            say(f"两条结算路径对 {decision.ticker} {decision.as_of} 不一致，该决策保持待决")
            continue
        final = await _with_lesson(decision, one, models, runs_root, limits, output_mode, say)
        log.resolve(final)
        outcomes.append(final)
        say(_outcome_line(final))
    return outcomes


async def _with_lesson(decision: DecisionRecord, outcome: OutcomeRecord,
                       models: Models | None, runs_root: Path | None, limits: Limits | None,
                       output_mode: OutputMode, say: Note) -> OutcomeRecord:
    """开一个小运行让复盘员写教训：审计通过则教训随结果落账，否则结果照常、教训留空。"""
    if models is None or runs_root is None:
        return outcome
    now = datetime.now()
    run_dir = runs_root / (f"{now:%Y%m%d-%H%M%S}-{file_stem(decision.ticker)}-{decision.as_of}-"
                           f"{secrets.token_hex(2)}")
    run_dir.mkdir(parents=True)
    journal = Journal(run_dir / "journal.jsonl",
                      now=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    run = Run(id=run_dir.name, journal=journal, board=Board(journal), models=models,
              budget=Budget(name="settle"), limits=limits or Limits(), output_mode=output_mode)
    journal.append("run.started", actor="settle", data={
        "request": {"ticker": decision.ticker, "as_of": decision.as_of},
        "known_by": outcome.known_on, "holding_days": outcome.holding_days})
    run.board.put("settle/decision", decision, by="settle")
    run.board.put("settle/outcome", outcome, by="settle")
    lesson_text: str | None = None
    try:
        lesson = await run_role(run, REFLECTOR)
        findings = lesson_findings(outcome, lesson)
        journal.append("gate.checked", actor="settle",
                       data={"gate": "settle", "findings": len(findings),
                             "detail": [{"check": f.check, "subject": f.subject,
                                         "message": f.message} for f in findings]})
        if findings:
            say(f"复盘员的教训未通过审计（{decision.ticker} {decision.as_of}），教训不落账")
        else:
            lesson_text = lesson.text
    except (Halt, Malformed) as e:
        say(f"复盘员不可用（{decision.ticker} {decision.as_of}）：{e}；结果照常落账，教训留空")
    final = outcome if lesson_text is None else outcome.model_copy(update={"lesson": lesson_text})
    run.board.put("settle/outcome", final, by="settle")
    journal.append("run.finished", actor="settle",
                   data={"status": "ok", "lesson": "written" if lesson_text else "none"})
    return final


def _stock_bars(deps: Deps, ticker: str, known_by: date,
                say: Note) -> tuple[Bar, ...] | None:
    """标的的 K 线（known_by 含之前）。读不出时无法结算，留在 pending 并汇报。"""
    try:
        return deps.market.history(ticker, known_by)
    except (UnknownTicker, NoData, SourceUnavailable) as e:
        say(f"读不出 {ticker} 到 {known_by} 的行情：{e}；该决策保持待决")
        return None


def _bench_bars(deps: Deps, benchmark: str, known_by: date) -> tuple[Bar, ...]:
    """基准的 K 线。宇宙里没有基准文件（合成数据无 SPY/_HSI）时给空：结果照算，
    基准两端无价则 benchmark_return_pct 与 alpha_pct 为 null（HANDOFF 问题 22 的读法）。"""
    try:
        return deps.market.history(benchmark, known_by)
    except (UnknownTicker, NoData, SourceUnavailable):
        return ()


def _outcome_line(outcome: OutcomeRecord) -> str:
    """一行结算汇报：代码、决策日、可知日、收益、基准与超额、先触及谁、教训是否落账。"""
    if outcome.alpha_pct is None or outcome.benchmark_return_pct is None:
        alpha = "无基准价"
    else:
        alpha = f"基准 {outcome.benchmark_return_pct:+.2f}%，超额 {outcome.alpha_pct:+.2f}%"
    hits = {"stop": "先触止损", "target": "先触目标", "neither": "两者都未触及", "n/a": "不适用"}
    lesson = "教训已落账" if outcome.lesson else "教训留空"
    return (f"{outcome.ticker} {outcome.as_of}：{outcome.known_on} 可知，"
            f"收益 {outcome.return_pct:+.2f}%，{alpha}，{hits[outcome.hit]}，{lesson}")
