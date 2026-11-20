"""版本 2.2 的评测格子（BLUEPRINT 19.16、19.18 WP38，20.14 修正）。

格子都是真实模型的运行：ACME（合成宇宙、逐个能力）与 AAPL、0700.HK（真实行情），
风险开/关、工具开/关、并行对串行、一次带组合、一次带课程。真实行情的格子没有
news/sentiment/macro 归档（线上数据源从未拉取），也没有报表与公告文件：那些通道
在真实格子里是 unavailable，结果表照实记（20.14 修正案）。

WP38 的运行（花费上限见 HANDOFF 第 4 节）：逐格串行执行，每格一行（结果、调用、
token、成本、通道状态、墙钟），成本随时累加，到 1.60 USD 不再起新格子。每格的
journal.jsonl 与 trace.jsonl 归档到 evals/archive/<run id>/；「并行对串行」的计时
对比全部取自轨迹（20.6：计时只进侧车文件，evals 不 import app，轨迹用标准库解析）。
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..flows.analyze import FlowConfig
from ..flows.deps import Deps
from ..kernel.runtime import Run

ARCHIVE = Path(__file__).resolve().parent / "archive"
STOP_USD = 1.60          # HANDOFF 第 4 节：到这条线不再起新格子，向 owner 汇报
CAP_USD = 2.00
_ANALYSTS = ("technical", "fundamentals", "disclosures", "news", "sentiment")


@dataclass(frozen=True, slots=True)
class Cell:
    """一个评测格子：一份请求 + 流程配置（+ 可选组合/记忆）。"""

    name: str                       # 格子名，如 "acme_all_on"
    ticker: str
    as_of: str                      # "" = 用最后一根已收盘 K 线
    universe: str                   # "synthetic" | "real"
    flow: FlowConfig
    portfolio: str = ""             # 组合文件路径；空 = 没有
    about: str = ""                 # 一句话：这个格子量什么


def cells() -> tuple[Cell, ...]:
    """19.16 的格子表。as_of 固定住，重跑才可比。"""
    acme = ("ACME", "2026-09-30", "synthetic")
    real_us, real_hk = ("AAPL", "2026-09-30", "real"), ("0700.HK", "2026-09-30", "real")
    on = FlowConfig(channels=("fundamentals", "disclosures", "news", "macro", "sentiment"),
                    risk_rounds=1, tools=True, memory=True)
    return (
        Cell("acme_all_on", *acme, on, about="全部能力开：一次跑通五项能力与并行分析"),
        Cell("acme_all_off", *acme, FlowConfig(), about="全部关：与 2.1 行为一致的基线"),
        Cell("acme_risk_on", *acme, FlowConfig(risk_rounds=1), about="只开风险辩论"),
        Cell("acme_tools_on", *acme, FlowConfig(tools=True), about="只开分析师工具"),
        Cell("acme_portfolio", *acme, FlowConfig(), portfolio="data/portfolios/flat_usd.json",
             about="带组合文件：规则与报告的组合节"),
        Cell("aapl_risk_on", *real_us, FlowConfig(risk_rounds=1), about="真实美股行情 + 风险"),
        Cell("aapl_tools_on", *real_us, FlowConfig(tools=True), about="真实美股行情 + 工具"),
        Cell("hk_all_on", *real_hk, on, about="真实港股行情 + 全部能力"),
        Cell("wall_serial", *acme, FlowConfig(parallel=False), about="串行墙钟（与并行格子对比）"),
    )


@dataclass(frozen=True, slots=True)
class CellResult:
    """一个格子的结果，全部取自它的日志、黑板与轨迹。"""

    cell: str
    ticker: str
    as_of: str
    universe: str
    run_id: str
    status: str                     # delivered / review（或 unexpected：格子内意外异常）
    reason: str
    rating: str | None
    calls: int
    retries: int
    tokens: int
    cost: float | None
    channel_states: dict[str, str]  # 通道 -> 状态（不可用时带一句原因）
    wall_ms: int | None             # 轨迹首行到末行的墙钟；无轨迹 = None
    analysts_sum_ms: int            # 分析师组各调用 ms 之和（内核测量）
    analysts_span_ms: int | None    # 分析师组首开到末收的墙钟跨度
    note: str = ""                  # 意外异常的说明（类名 + 一句话）


def no_spend_reason() -> str:
    """为什么现在不跑：等 owner 的花费批准与上限（HANDOFF 问题 14 已答：2 USD）。"""
    return "v22 的格子都是真实模型运行：owner 已给 2 USD 上限（问题 14），WP38 在此上限内执行。"


# ---- 读一份运行：日志、黑板、轨迹 ----


def _read_cell(cell: Cell, run: Run, status: str, reason: str, out_dir: Path | None) -> CellResult:
    """运行结束后读数。数字只来自日志的 model.called 与 run/usage，计时只来自轨迹。"""
    events = run.journal.events
    calls = [e for e in events if e.type == "model.called"]
    costs = [e.data.get("cost") for e in calls]
    rating = None
    if status == "delivered" and run.board.has("manager/thesis"):
        rating = run.board.get("manager/thesis").get("rating")
    states: dict[str, str] = {}
    for channel in cell.flow.channels:
        if run.board.has(f"{channel}/status"):
            cs = run.board.get(f"{channel}/status")
            label = cs.get("state") if isinstance(cs, dict) else getattr(cs, "state", "?")
            why = cs.get("reason") if isinstance(cs, dict) else getattr(cs, "reason", "")
            states[channel] = f"{label}" + (f"（{why}）" if why else "")
    wall_ms, a_sum, a_span = _timing(out_dir / "trace.jsonl") if out_dir else (None, 0, None)
    return CellResult(
        cell=cell.name, ticker=cell.ticker, as_of=cell.as_of, universe=cell.universe,
        run_id=run.id, status=status, reason=reason, rating=rating,
        calls=len(calls),
        retries=sum(e.data.get("usage", {}).get("requests", 1) - 1 for e in calls),
        tokens=sum(e.data.get("usage", {}).get("input_tokens", 0)
                   + e.data.get("usage", {}).get("output_tokens", 0) for e in calls),
        cost=None if any(c is None for c in costs)
        else round(sum(c for c in costs if c is not None), 6),
        channel_states=states, wall_ms=wall_ms, analysts_sum_ms=a_sum, analysts_span_ms=a_span)


def _timing(trace_path: Path) -> tuple[int | None, int, int | None]:
    """从轨迹算计时：(整份墙钟 ms, 分析师组 ms 之和, 分析师组跨度 ms)。

    解析不过的行跳过、缺文件当空（与 app/trace.load 同规矩，但这里只用标准库：
    evals 不得 import app）。墙钟取首末行 ts 之差；分析师组之和取 role.finished 的
    ms（内核测量）；跨度取组内最早 role.started 到最晚 role.finished 的 ts 之差。
    """
    try:
        lines = _load_trace(trace_path)
    except OSError:
        return None, 0, None
    if not lines:
        return None, 0, None
    wall_ms = _ms(lines[-1]["ts"]) - _ms(lines[0]["ts"])
    a_sum, starts, ends = 0, [], []
    open_spans: dict[tuple[str, str], str] = {}
    for line in lines:
        kind, role = line.get("kind"), line.get("role", "")
        if kind == "role.started":
            open_spans[(str(line.get("step")), role)] = line["ts"]
            if role in _ANALYSTS:
                starts.append(line["ts"])
        elif kind == "role.finished":
            open_spans.pop((str(line.get("step")), role), None)
            if role in _ANALYSTS:
                a_sum += int(line.get("ms") or 0)
                ends.append(line["ts"])
        elif kind == "role.failed":
            open_spans.pop((str(line.get("step")), role), None)
    span_ms = (_ms(max(ends)) - _ms(min(starts))) if starts and ends else None
    return wall_ms, a_sum, span_ms


def _load_trace(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict) and "ts" in parsed:
            rows.append(parsed)
    return rows


def _ms(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp() * 1000)


# ---- 归档 ----


def archive_run(out_dir: Path, archive_root: Path = ARCHIVE) -> Path:
    """把 journal.jsonl 与 trace.jsonl 归档到 evals/archive/<run id>/（20.14 修正案）。"""
    out = archive_root / out_dir.name
    out.mkdir(parents=True, exist_ok=True)
    for name in ("journal.jsonl", "trace.jsonl"):
        src = out_dir / name
        if src.is_file():
            shutil.copyfile(src, out / name)
    return out


# ---- 表格 ----


def _s(ms: int | None) -> str:
    return "未测量" if ms is None else f"{ms / 1000:.1f}s"


def results_table(results: list[CellResult]) -> str:
    """一行一格的结果表（19.18 WP38 验收：结果表进会话日志）。"""
    lines = ["| 格子 | 数据集 | 结果 | 原因 | 评级 | 调用 | 重试 | token | 成本 USD | 通道 | 墙钟 | 分析师组和/跨度 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        channels = "、".join(f"{k}:{v}" for k, v in r.channel_states.items()) or "-"
        cost = "未定价" if r.cost is None else f"{r.cost:.4f}"
        rating = r.rating or "-"
        lines.append(
            f"| {r.cell} | {r.ticker} @{r.as_of}（{r.universe}） | {r.status} | {r.reason} | {rating} "
            f"| {r.calls} | {r.retries} | {r.tokens} | {cost} | {channels} | {_s(r.wall_ms)} "
            f"| {_s(r.analysts_sum_ms)} / {_s(r.analysts_span_ms)} |")
    return "\n".join(lines)


def timing_compare(results: list[CellResult]) -> str:
    """「并行对串行」的计时对比（19.16），全部取自轨迹。

    最小同形一对：acme_all_off（并行）对 wall_serial（串行），能力全关，只差并行开关；
    全能力运行里分析师组的重叠：acme_all_on 的组内各调用之和 对 组跨度（并行时跨度
    应明显小于和）。没有轨迹的格子不参与。
    """
    by_name = {r.cell: r for r in results}

    def pair(parallel: str, serial: str) -> str:
        p, s = by_name.get(parallel), by_name.get(serial)
        if p is None or s is None or p.wall_ms is None or s.wall_ms is None:
            return f"{parallel} 对 {serial}：缺运行或缺轨迹，未测。"
        diff = (p.wall_ms - s.wall_ms) / 1000
        return (f"{parallel}（并行）{_s(p.wall_ms)} 对 {serial}（串行）{_s(s.wall_ms)}："
                f"并行{'快' if diff < 0 else '慢'} {abs(diff):.1f}s")

    on = by_name.get("acme_all_on")
    overlap = "acme_all_on 缺运行或缺轨迹，未测。"
    if on is not None and on.analysts_span_ms:
        saved = (on.analysts_sum_ms - on.analysts_span_ms) / 1000
        overlap = (f"acme_all_on 分析师组：各调用之和 {_s(on.analysts_sum_ms)}，"
                   f"组跨度 {_s(on.analysts_span_ms)}（并行重叠省约 {saved:.1f}s）")
    return "\n".join([pair("acme_all_off", "wall_serial"), overlap])


# ---- 运行 ----


async def run_cells(open_run: Callable[..., tuple[Run, Deps]],
                    analyze: Callable[..., Awaitable[Any]],
                    to_run: tuple[Cell, ...] = (),
                    *, archive_root: Path = ARCHIVE,
                    stop_usd: float = STOP_USD) -> list[CellResult]:
    """逐格串行执行（花费可控、计时不受并行运行挤占），每格一行，归档 journal+trace。"""
    results: list[CellResult] = []
    total = 0.0
    for cell in to_run or cells():
        if total >= stop_usd:
            print(f"已花 {total:.4f} USD ≥ {stop_usd:.2f} USD：停止起新格子（HANDOFF 上限规则）。")
            break
        request = {"ticker": cell.ticker, "as_of": cell.as_of}
        try:
            run, deps = open_run(request, flow=cell.flow, universe=cell.universe,
                                 portfolio=cell.portfolio)
            outcome = await analyze(run, request, deps, cell.flow)
            result = _read_cell(cell, run, outcome.status, outcome.reason, deps.out_dir)
        except Exception as e:                    # 意外异常：记录这一格，继续下一格
            results.append(CellResult(cell.name, cell.ticker, cell.as_of, cell.universe,
                                      "?", "unexpected", type(e).__name__, None, 0, 0, 0, None,
                                      {}, None, 0, None, f"{type(e).__name__}: {e}"))
            print(f"  {cell.name}: 意外异常 {type(e).__name__}，跳过归档", flush=True)
            continue
        if deps.out_dir is not None:               # open_run 一律建目录；静态上可空，运行中必有
            archive_run(deps.out_dir, archive_root)
        results.append(result)
        total += result.cost or 0.0
        cost = "未定价" if result.cost is None else f"{result.cost:.4f}"
        print(f"  {cell.name:<14} {result.ticker:<8} {result.status:<9} {result.reason:<12} "
              f"调用 {result.calls}  token {result.tokens}  成本 {cost}  累计 {total:.4f} USD  "
              f"{result.run_id}", flush=True)
    return results


async def main(open_run: Callable[..., tuple[Run, Deps]],
               analyze: Callable[..., Awaitable[Any]], base: FlowConfig | None = None,
               *, archive_root: Path = ARCHIVE) -> int:
    """eval v22 的入口：跑 19.16 的九个格子。base 不用（格子自带流程配置），签名与调用方对齐。"""
    del base                                    # 格子的 FlowConfig 是格子自己的，不从设置出发
    all_cells = cells()
    print(f"v22 评测：{len(all_cells)} 个格子，串行执行；花费上限 {CAP_USD:.2f} USD，"
          f"{STOP_USD:.2f} USD 起不再起新格子。真实行情格子的 news/sentiment/macro 通道"
          f"没有归档（线上数据源从未拉取），一律 unavailable。")
    results = await run_cells(open_run, analyze, all_cells, archive_root=archive_root)
    print(results_table(results))
    print(timing_compare(results))
    total = sum(r.cost or 0.0 for r in results)
    out = write_archive(archive_root, results)
    print(f"合计成本 {total:.4f} USD（上限 {CAP_USD:.2f}，停下线 {STOP_USD:.2f}）；"
          f"明细已归档到 {archive_root}/<run id>/（journal.jsonl + trace.jsonl），"
          f"汇总在 {out.name}。")
    return 1 if any(r.status == "unexpected" for r in results) else 0


def write_archive(archive_root: Path, results: list[CellResult]) -> Path:
    """格子明细与汇总写进 archive/v22.json（与 ablation.json、channels.json 同款）。"""
    archive_root.mkdir(parents=True, exist_ok=True)
    out = archive_root / "v22.json"
    out.write_text(json.dumps({"cells": [asdict(r) for r in results]},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
