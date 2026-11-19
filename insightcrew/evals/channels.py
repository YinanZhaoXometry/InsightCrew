"""通道评测（WP19）：同一批（代码，分析日，数据集）格子，在三种通道配置下各跑一次真实模型。

回答 BLUEPRINT 18.13 的问题：打开证据通道后，结果、过程与成本变不变，论点有多少
真的引用了通道的证据。每个配置报告：交付数、REVIEW 率、每次返工、发现按检查项的计数、
每次调用与 token、每次成本、以及论点引用通道证据的比例。每个数字都来自一份日志，
汇总与逐格明细写进 archive/channels.json。

需要真实模型。evals 不 import app：开运行的函数由调用方交进来；AAPL 与 0700.HK 的
格子用 real 数据集，调用方给的 open_run 要接受 universe 关键字来切换数据集。
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.runtime import Run

ARCHIVE = Path(__file__).resolve().parent / "archive"
# （代码， 分析日 | None = 最后已收盘 K 线, 数据集）
CELLS: tuple[tuple[str, str | None, str], ...] = (
    ("ACME", "2026-09-30", "synthetic"),
    ("ACME", "2026-05-29", "synthetic"),
    ("AAPL", None, "real"),
    ("0700.HK", None, "real"),
)
WIDTH = 3                                                # 同时进行的运行数


def configs(base: FlowConfig) -> dict[str, FlowConfig]:
    """三种通道配置，其余一切从设置里的流程配置出发。"""
    return {
        "none": base.model_copy(update={"channels": ()}),
        "fundamentals": base.model_copy(update={"channels": ("fundamentals",)}),
        "both": base.model_copy(update={"channels": ("fundamentals", "disclosures")}),
    }


@dataclass(frozen=True, slots=True)
class Cell:
    """一次运行的结果，全部取自它的日志。"""

    config: str
    ticker: str
    as_of: str
    universe: str
    run_id: str
    status: str
    reason: str
    rating: str | None
    revisions: int
    calls: int
    retries: int
    tokens: int
    cost: float | None
    findings: dict[str, int]                             # 检查项 -> 该运行全部发现里的次数
    channel_states: dict[str, str]                       # 通道 -> 状态（没开就没有键）
    points: int                                          # 论点条数
    channel_points: int                                  # 引用了通道证据的论点条数


def read_cell(config: str, ticker: str, universe: str, as_of: str,
              run: Run, status: str, reason: str) -> Cell:
    events = run.journal.events
    calls = [e for e in events if e.type == "model.called"]
    costs = [e.data.get("cost") for e in calls]
    rating = None
    if status == "delivered" and run.board.has("manager/thesis"):
        thesis = run.board.get("manager/thesis")
        rating = thesis.get("rating")
        # 通道证据的 ID：基本面 claim 引用过的事实 + 公告信号解读过的条目。
        cited: set[str] = set()
        for key, ids_key in (("fundamentals/analysis", "claims"), ("disclosures/analysis", "signals")):
            analysis = run.board.get(key) if run.board.has(key) else None
            if analysis:
                for item in analysis.get(ids_key) or ():
                    cited.add(item.get("fact") or item.get("item") or "")
        points = list(thesis.get("points") or ())
        channel_points = sum(1 for p in points
                             if cited and set(p.get("cites") or ()) & cited)
    else:
        points, channel_points = [], 0
    states = {}
    for channel in ("fundamentals", "disclosures"):
        if run.board.has(f"{channel}/status"):
            states[channel] = run.board.get(f"{channel}/status").get("state")
    return Cell(
        config=config, ticker=ticker, as_of=as_of, universe=universe, run_id=run.id,
        status=status, reason=reason, rating=rating,
        revisions=sum(e.type == "route" for e in events),
        calls=len(calls),
        retries=sum(e.data.get("usage", {}).get("requests", 1) - 1 for e in calls),
        tokens=sum(e.data.get("usage", {}).get("input_tokens", 0)
                   + e.data.get("usage", {}).get("output_tokens", 0) for e in calls),
        cost=None if any(c is None for c in costs)
        else round(sum(c for c in costs if c is not None), 6),
        findings=dict(Counter(f.get("check", "") for e in events
                              if e.type == "artifact.written" and e.data.get("key") == "audit/findings"
                              for f in e.data.get("value") or ())),
        channel_states=states, points=len(points), channel_points=channel_points,
    )


async def run_cells(open_run: Callable[..., tuple[Run, Deps]],
                    analyze: Callable[..., Awaitable[Any]], base: FlowConfig,
                    cells: Sequence[tuple[str, str | None, str]] = CELLS,
                    width: int = WIDTH) -> list[Cell]:
    gate = asyncio.Semaphore(width)

    async def one(name: str, cfg: FlowConfig, ticker: str, as_of: str | None, universe: str) -> Cell:
        async with gate:
            request: dict[str, str] = {"ticker": ticker}
            if as_of:
                request["as_of"] = as_of
            run, deps = open_run(request, flow=cfg, universe=universe)
            outcome = await analyze(run, request, deps, cfg)
            day = as_of or run.board.get("request/scope").get("as_of", "")
            cell = read_cell(name, ticker, universe, day, run, outcome.status, outcome.reason)
            states = "、".join(f"{k}:{v}" for k, v in cell.channel_states.items()) or "-"
            print(f"  {name:<13} {ticker:<8} {day}  {cell.status:<9} {cell.reason:<10} "
                  f"返工 {cell.revisions}  调用 {cell.calls}  通道 {states}  {run.id}", flush=True)
            return cell

    jobs = [one(name, cfg, ticker, as_of, universe)
            for name, cfg in configs(base).items() for ticker, as_of, universe in cells]
    return list(await asyncio.gather(*jobs))


def summarise(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    rows = []
    for name in dict.fromkeys(c.config for c in cells):
        group = [c for c in cells if c.config == name]
        costs = [c.cost for c in group]
        points = sum(c.points for c in group)
        channel_points = sum(c.channel_points for c in group)
        findings: Counter[str] = Counter()
        for c in group:
            findings.update(c.findings)
        rows.append({
            "config": name,
            "runs": len(group),
            "delivered": sum(c.status == "delivered" for c in group),
            "review_rate": round(sum(c.status == "review" for c in group) / len(group), 3),
            "revisions_per_run": round(sum(c.revisions for c in group) / len(group), 2),
            "retries_per_run": round(sum(c.retries for c in group) / len(group), 2),
            "calls_per_run": round(sum(c.calls for c in group) / len(group), 2),
            "tokens_per_run": round(sum(c.tokens for c in group) / len(group)),
            "cost_per_run": None if any(c is None for c in costs)
            else round(sum(c for c in costs if c is not None) / len(group), 4),
            "findings_by_check": dict(findings),
            "channel_points": f"{channel_points}/{points}",
        })
    return rows


def table(rows: Sequence[dict[str, Any]]) -> str:
    lines = ["| 配置 | 运行 | 交付 | REVIEW 率 | 每次返工 | 每次调用 | 每次 token | 每次成本 "
             "| 论点引用通道证据 | 发现（按检查项） |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cost = "未定价" if r["cost_per_run"] is None else f"{r['cost_per_run']:.4f}"
        findings = "无" if not r["findings_by_check"] else \
            "、".join(f"{k}×{v}" for k, v in r["findings_by_check"].items())
        lines.append(f"| {r['config']} | {r['runs']} | {r['delivered']} | {r['review_rate']:.0%} | "
                     f"{r['revisions_per_run']} | {r['calls_per_run']} | {r['tokens_per_run']} | "
                     f"{cost} | {r['channel_points']} | {findings} |")
    return "\n".join(lines)


async def main(open_run: Callable[..., tuple[Run, Deps]], analyze: Callable[..., Awaitable[Any]],
               base: FlowConfig, archive: Path = ARCHIVE) -> int:
    print(f"通道评测：{len(CELLS)} 个格子 × 3 种配置，同时 {WIDTH} 个运行")
    cells = await run_cells(open_run, analyze, base, CELLS)
    rows = summarise(cells)
    print(table(rows))
    out = write_archive(archive, rows, cells)
    print(f"每个格子的运行 ID 与汇总已写入 {out.name}")
    return 0


def write_archive(archive: Path, rows: Sequence[dict[str, Any]], cells: Sequence[Cell]) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / "channels.json"
    out.write_text(json.dumps({"summary": list(rows), "cells": [asdict(c) for c in cells]},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
