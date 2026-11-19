# Adapted from TradingAgents (Apache-2.0), modified.
"""backtest 命令（BLUEPRINT 19.13）：按交易日网格逐日先结算再分析（记忆开着）。

改写自 TradingAgents backtest.py 的网格迭代、可续跑进度与汇总口径，重写为本项目的
命令与契约：网格来自 deps.market 的交易日；每个格子先以 known_by=当日 结算此前的
决策，再以 memory=True 分析当日（不变量 20：晚于当日的教训绝不可见）。进度在
runs/backtest/<run_id>/progress.json（原子写），同名的批次接着跑，已完成的格子跳过；
本批次自己的决策流水在 decisions.jsonl；批次预算（[budget] batch_*）超限即停。
配置了真实模型时没有 --yes 一律拒绝。汇总按评级打印：笔数、已结算数、平均收益、
平均超额、先触止损与先触目标的次数。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from ..adapters.decision_log import DecisionLog
from ..domain.market import NoData, SourceUnavailable, UnknownTicker
from ..domain.memory import OutcomeRecord
from ..domain.types import Proposal, Scope, Thesis
from ..flows.settle import settle as settle_flow
from ..kernel.budget import Budget, Ceiling
from ..kernel.runtime import Limits
from .settings import Settings
from .wiring import ROOT, _models, build_deps, describe_models, open_run, prepare, run_flow


def run(settings: Settings, tickers: tuple[str, ...], from_date: str, to_date: str,
        every: int, run_id: str, yes: bool, *, runs_root: Path | None = None,
        settle_root: Path | None = None) -> int:
    """命令入口。日期无法解析、范围颠倒、代码不在数据集里、真实模型未确认：退出码 2。"""
    try:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    except ValueError:
        print(f"日期无法解析：--from {from_date!r} --to {to_date!r}，请写成 YYYY-MM-DD")
        return 2
    if start > end:
        print(f"--from {from_date} 晚于 --to {to_date}，回测范围不成立")
        return 2
    mode, _names, _reason = describe_models(settings)
    if mode == "model" and not yes:
        print("配置了真实模型：回测会逐格调用模型花钱。确认请加 --yes。")
        return 2
    return asyncio.run(_backtest(settings, tickers, start, end, every,
                                 run_id or f"{from_date}-{to_date}",
                                 runs_root or ROOT / "runs" / "backtest",
                                 settle_root or ROOT / "runs" / "memory" / "settle"))


async def _backtest(settings: Settings, tickers: tuple[str, ...], start: date, end: date,
                    every: int, run_id: str, batch_root: Path, settle_root: Path) -> int:
    """网格走查：先结算、再分析、记进度；批次预算超限即停，收尾把范围末端也结一遍。"""
    deps = build_deps(settings)
    grids: dict[str, list[date]] = {}
    for ticker in tickers:
        try:
            days = [b.date for b in deps.market.history(ticker, end) if b.date >= start]
        except (UnknownTicker, NoData, SourceUnavailable) as e:
            print(f"读不出 {ticker} 到 {end} 的行情：{e}")
            return 2
        grids[ticker] = days[::every]
    total = sum(len(days) for days in grids.values())
    if total == 0:
        print(f"{start} 到 {end} 之间没有交易日，无事可做")
        return 0

    batch_dir = batch_root / run_id
    progress_path = batch_dir / "progress.json"
    decisions_path = batch_dir / "decisions.jsonl"
    progress = _load_progress(progress_path)
    if progress is not None:
        same = (progress.get("from") == start.isoformat() and progress.get("to") == end.isoformat()
                and progress.get("every") == every
                and tuple(progress.get("tickers") or ()) == tickers)
        if not same:
            print(f"{progress_path} 是另一个范围或另一组代码的批次进度；换一个 --run-id 再跑")
            return 2
    else:
        batch_dir.mkdir(parents=True, exist_ok=True)
        progress = {"run_id": run_id, "from": start.isoformat(), "to": end.isoformat(),
                    "every": every, "tickers": list(tickers), "cells": {}}
        _atomic_write(progress_path, json.dumps(progress, ensure_ascii=False, indent=2) + "\n")

    log_path = ROOT / settings.memory.path
    log_path.parent.mkdir(parents=True, exist_ok=True)   # DecisionLog 的锁文件先于 _write 建目录
    log = DecisionLog(log_path, max_entries=settings.memory.max_entries)
    models, _mode, _names = _models(settings, None, None)
    limits = Limits(max_requests=settings.limits.max_requests,
                    max_seconds=settings.limits.max_seconds,
                    request_timeout_s=settings.limits.request_timeout_s)
    batch = Budget(Ceiling(tokens=settings.budget.batch_tokens or None,
                           cost=settings.budget.batch_cost or None), name="batch")
    print(f"回测 {run_id}：{total} 个格子，已完成 {len(progress['cells'])} 个"
          f"（每个日期先结算再分析，记忆开着）")

    async def settle_to(day: date) -> None:
        await settle_flow(deps, log, day, holding_days=settings.memory.holding_days,
                          models=models, runs_root=settle_root, limits=limits,
                          output_mode=settings.run.output_mode, note=print)

    stopped = ""
    for ticker in tickers:
        for day in grids[ticker]:
            key = f"{ticker}@{day.isoformat()}"
            if key in progress["cells"]:
                continue                                  # 续跑：已完成的格子跳过
            if scope := batch.exceeded():
                stopped = scope
                break
            await settle_to(day)                          # 19.13：先结算，再分析
            job = prepare(settings, ticker=ticker, as_of=day.isoformat(), memory=True)
            run, _rdeps = open_run(settings, job.request, flow=job.flow, batch=batch,
                                   runs_dir=batch_dir)
            outcome = await run_flow(run, job.request, _rdeps, job.flow)
            progress["cells"][key] = {"status": outcome.status, "reason": outcome.reason,
                                      "run": run.id}
            if outcome.status == "delivered":
                _append_line(decisions_path, _decision_line(run.id, run))
            _atomic_write(progress_path, json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
            print(f"· {key}：{outcome.status}（{outcome.reason}）运行 {run.id}", flush=True)
        if stopped:
            break
    if stopped:
        print(f"批次预算超限（{stopped}），网格在此停住；加预算后用同一 --run-id 续跑")
    else:
        await settle_to(end)                              # 收尾：范围内已走完的窗口也结掉
    _summary(decisions_path, log)
    print(f"回测目录：{batch_dir}；决策日志：{ROOT / settings.memory.path}")
    return 0


def _decision_line(run_id: str, run: Any) -> str:
    """一行决策流水：交付格子的评级与方案字段，与 DecisionRecord 同口径。"""
    scope = run.board.get("request/scope", Scope)
    thesis = run.board.get("manager/thesis", Thesis)
    proposal = run.board.get("trader/proposal", Proposal)
    return json.dumps({"run_id": run_id, "ticker": scope.ticker, "as_of": scope.as_of,
                       "rating": str(thesis.rating), "action": str(proposal.action),
                       "entry": proposal.entry, "stop": proposal.stop,
                       "target": proposal.target, "size_pct": proposal.size_pct,
                       "horizon_days": proposal.horizon_days}, ensure_ascii=False)


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _atomic_write(path: Path, text: str) -> None:
    """原子写：先写旁边的临时文件，再 os.replace 换入；读者只见整份新或整份旧。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _load_progress(path: Path) -> dict[str, Any] | None:
    """读批次进度；不存在返回 None。范围与代码是否相符由调用方核对。"""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _summary(decisions_path: Path, log: DecisionLog) -> None:
    """按评级汇总本批次的决策：笔数、已结算数、平均收益与超额、先触止损/目标的次数。"""
    if not decisions_path.exists():
        print("没有交付的决策，无汇总")
        return
    decisions = [json.loads(line) for line in
                 decisions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    outcomes = {(r.as_of, r.ticker): r for r in log.records() if isinstance(r, OutcomeRecord)}
    print(f"按评级汇总（{len(decisions)} 笔交付的决策）：")
    for rating in dict.fromkeys(str(d["rating"]) for d in decisions):
        group = [d for d in decisions if d["rating"] == rating]
        settled = [outcomes[(d["as_of"], d["ticker"])] for d in group
                   if (d["as_of"], d["ticker"]) in outcomes]
        returns = [o.return_pct for o in settled]
        alphas = [o.alpha_pct for o in settled if o.alpha_pct is not None]
        mean_return = f"{sum(returns) / len(returns):+.2f}%" if returns else "尚无已结算"
        if not settled:
            mean_alpha = "尚无已结算"
        elif alphas:
            mean_alpha = f"{sum(alphas) / len(alphas):+.2f}%"
        else:
            mean_alpha = "无基准价"
        stops = sum(o.hit == "stop" for o in settled)
        targets = sum(o.hit == "target" for o in settled)
        print(f"  {rating}：{len(group)} 笔，已结算 {len(settled)}，"
              f"平均收益 {mean_return}，平均超额 {mean_alpha}，先触止损 {stops} 次，"
              f"先触目标 {targets} 次")
