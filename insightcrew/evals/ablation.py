"""消融评测（WP8）：同一批（代码，分析日）格子，在四种流程配置下各跑一次真实模型。

回答 BLUEPRINT 11.3 的问题：拿掉一块脚手架，结果变不变。每个配置报告：交付的方案里
审计会拒绝的比例（只有 shadow 配置可能非零：审计照跑照记，但不打回）、REVIEW 率、
返工次数、每次运行的 token 与成本。每个数字都来自一份日志，汇总写进 archive/ablation.json。

需要真实模型。evals 不 import app：开运行的函数与流程入口由调用方交进来。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..domain.types import Thesis
from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.runtime import Run

ARCHIVE = Path(__file__).resolve().parent / "archive"
CELLS: tuple[tuple[str, str], ...] = (
    ("ACME", "2026-09-30"), ("ACME", "2026-05-29"),
    ("0000.HK", "2026-09-30"), ("0000.HK", "2026-04-30"),
)
WIDTH = 4                                                # 同时进行的运行数


def configs(base: FlowConfig) -> dict[str, FlowConfig]:
    """四种配置，都从设置里的流程配置出发，只改被消融的那几项。"""
    return {
        "full": base,
        "no_judges": base.model_copy(update={"judges": False}),
        "shadow": base.model_copy(update={"audit": "shadow", "judges": False}),
        "bare": base.model_copy(update={"audit": "shadow", "judges": False, "debate_rounds": 0}),
    }


@dataclass(frozen=True, slots=True)
class Cell:
    """一次运行的结果，全部取自它的日志。"""

    config: str
    ticker: str
    as_of: str
    run_id: str
    status: str
    reason: str
    rating: str | None
    would_reject: bool                                   # 交付了，但最后一次审计有发现
    revisions: int
    calls: int
    retries: int                                         # 纠正重试：requests 减 calls
    tokens: int
    cost: float | None                                   # 有未定价的调用就是 None


def read_cell(config: str, ticker: str, as_of: str, run: Run, status: str, reason: str) -> Cell:
    events = run.journal.events
    calls = [e for e in events if e.type == "model.called"]
    audits = [e for e in events if e.type == "gate.checked" and e.data.get("gate") == "audit"]
    costs = [e.data.get("cost") for e in calls]
    rating = None
    if status == "delivered" and run.board.has("manager/thesis"):
        rating = run.board.get("manager/thesis", Thesis).rating
    return Cell(
        config=config, ticker=ticker, as_of=as_of, run_id=run.id, status=status, reason=reason,
        rating=rating,
        would_reject=status == "delivered" and bool(audits) and audits[-1].data.get("findings", 0) > 0,
        revisions=sum(e.type == "route" for e in events),
        calls=len(calls),
        retries=sum(e.data.get("usage", {}).get("requests", 1) - 1 for e in calls),
        tokens=sum(e.data.get("usage", {}).get("input_tokens", 0) + e.data.get("usage", {}).get("output_tokens", 0)
                   for e in calls),
        cost=None if any(c is None for c in costs) else round(sum(c for c in costs if c is not None), 6),
    )


async def run_cells(open_run: Callable[..., tuple[Run, Deps]], analyze: Callable[..., Awaitable[Any]],
                    base: FlowConfig, cells: Sequence[tuple[str, str]] = CELLS,
                    width: int = WIDTH) -> list[Cell]:
    gate = asyncio.Semaphore(width)

    async def one(name: str, cfg: FlowConfig, ticker: str, as_of: str) -> Cell:
        async with gate:
            request = {"ticker": ticker, "as_of": as_of}
            run, deps = open_run(request, flow=cfg)
            outcome = await analyze(run, request, deps, cfg)
            cell = read_cell(name, ticker, as_of, run, outcome.status, outcome.reason)
            print(f"  {name:<10} {ticker:<8} {as_of}  {cell.status:<9} {cell.reason:<18} "
                  f"返工 {cell.revisions}  token {cell.tokens}  {run.id}", flush=True)
            return cell

    jobs = [one(name, cfg, ticker, as_of) for name, cfg in configs(base).items() for ticker, as_of in cells]
    return list(await asyncio.gather(*jobs))


def summarise(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    rows = []
    for name in dict.fromkeys(c.config for c in cells):
        group = [c for c in cells if c.config == name]
        delivered = [c for c in group if c.status == "delivered"]
        costs = [c.cost for c in group]
        rows.append({
            "config": name,
            "runs": len(group),
            "delivered": len(delivered),
            "would_reject": sum(c.would_reject for c in delivered),
            "review_rate": round(sum(c.status == "review" for c in group) / len(group), 3),
            "revisions_per_run": round(sum(c.revisions for c in group) / len(group), 2),
            "retries_per_run": round(sum(c.retries for c in group) / len(group), 2),
            "tokens_per_run": round(sum(c.tokens for c in group) / len(group)),
            "cost_per_run": None if any(c is None for c in costs)
            else round(sum(c for c in costs if c is not None) / len(group), 4),
        })
    return rows


def table(rows: Sequence[dict[str, Any]]) -> str:
    lines = ["| 配置 | 运行 | 交付 | 交付中审计会拒绝 | REVIEW 率 | 每次返工 | 每次纠正重试 | 每次 token | 每次成本 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cost = "未定价" if r["cost_per_run"] is None else f"{r['cost_per_run']:.4f}"
        lines.append(f"| {r['config']} | {r['runs']} | {r['delivered']} | {r['would_reject']}/{r['delivered']} | "
                     f"{r['review_rate']:.0%} | {r['revisions_per_run']} | {r['retries_per_run']} | "
                     f"{r['tokens_per_run']} | {cost} |")
    return "\n".join(lines)


async def main(open_run: Callable[..., tuple[Run, Deps]], analyze: Callable[..., Awaitable[Any]],
               base: FlowConfig, archive: Path = ARCHIVE) -> int:
    print(f"消融评测：{len(CELLS)} 个格子 × 4 种配置，同时 {WIDTH} 个运行")
    cells = await run_cells(open_run, analyze, base, CELLS)
    rows = summarise(cells)
    print(table(rows))
    out = write_archive(archive, rows, cells)
    print(f"每个格子的运行 ID 与汇总已写入 {out.name}")
    return 0


def write_archive(archive: Path, rows: Sequence[dict[str, Any]], cells: Sequence[Cell]) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / "ablation.json"
    out.write_text(json.dumps({"summary": list(rows), "cells": [asdict(c) for c in cells]},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
