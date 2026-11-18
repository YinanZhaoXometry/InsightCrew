"""命令行。每个命令装配设置、调用下层、打印结果；跑流程的命令用 asyncio.run。

cli.py 在 WP5 一次写全：check、replay、eval、fetch 这四个命令是留给后续工作包的空壳，
cli.py 调它们各自模块里的 run 函数；后续工作包只填函数，不改本文件。
选项用 Annotated 写法：默认值保持字面量，Typer 的说明放在注解里。
"""
from __future__ import annotations

import asyncio
import webbrowser
from datetime import date as date_type
from functools import partial
from pathlib import Path
from typing import Annotated

import typer

from ..domain import symbols
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import InvalidTicker, NoData, SourceUnavailable, UnknownTicker
from ..domain.types import Outcome
from ..evals import run as eval_cmd
from ..evals.scenarios import load_one, run_all, summary_line
from ..evals.scenarios import names as scenario_names
from ..flows.analyze import FlowConfig
from ..flows.analyze import analyze as run_flow
from ..flows.deps import Deps
from ..kernel.runtime import Run
from . import check as check_cmd
from . import fetch as fetch_cmd
from . import replay as replay_cmd
from . import replay_html
from . import server as server_cmd
from .settings import load
from .wiring import ROOT, build_deps, open_run

app = typer.Typer(help="InsightCrew：带独立审计的多智能体交易辅助系统。没有密钥时运行离线演示。",
                  no_args_is_help=True)

_GATE = {"intake": "范围", "ledger": "账本", "audit": "审计"}


@app.command()
def analyze(
    ticker: Annotated[str, typer.Argument(help="代码，如 ACME 或 0700.HK；不给则运行演示场景")] = "",
    date: Annotated[str, typer.Option("--date", help="分析日（ISO）；不给则用最后一根已收盘K线")] = "",
    scenario: Annotated[str, typer.Option("--scenario", help="用预先写好的回答跑一个场景")] = "",
    universe: Annotated[str, typer.Option("--universe", help="数据集名字，默认取设置文件")] = "",
    no_judges: Annotated[bool, typer.Option("--no-judges", help="跳过模型评审")] = False,
    debate_rounds: Annotated[int | None, typer.Option("--debate-rounds", help="辩论轮数")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="只打印结果，不打印过程")] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="不打开运行页面")] = False,
) -> None:
    """跑一次分析：给出评级与交易方案，或者说明为什么只能 REVIEW。"""
    overrides: dict[str, object] = {}
    if universe:
        overrides["run"] = {"universe": universe}
    flow_over: dict[str, object] = {}
    if no_judges:
        flow_over["judges"] = False
    if debate_rounds is not None:
        flow_over["debate_rounds"] = debate_rounds
    if flow_over:
        overrides["flow"] = flow_over
    settings = load(ROOT, **overrides)

    request: dict[str, str] = {}
    if ticker:
        request["ticker"] = ticker
    if date:
        request["as_of"] = date
    scenario_obj = load_one(scenario) if scenario else None

    flow = FlowConfig(**{**settings.flow.model_dump(), **flow_over})
    run, deps = open_run(settings, request, scenario=scenario_obj, flow=flow)
    outcome = asyncio.run(run_flow(run, request, deps, flow))

    page_path = _write_run_page(run, deps)
    if not quiet:
        _print_process(run)
    print(_outcome_line(outcome))
    print(f"运行目录：{deps.out_dir}")
    if outcome.reason == "model_unavailable":
        print(f"没有可用的模型（原因见上方）。离线场景：{'、'.join(scenario_names())}")
    if page_path is not None and not no_open:
        webbrowser.open(page_path.as_uri())


def _write_run_page(run: Run, deps: Deps) -> Path | None:
    """把运行页面写进运行目录；页面是日志的投影。拿不到价格序列就不嵌图。"""
    if deps.out_dir is None:
        return None
    bars: list[dict] | None = None
    try:
        board = run.board
        if board.has("request/scope"):
            scope = board.get("request/scope")
            series = deps.market.history(scope["ticker"], date_type.fromisoformat(scope["as_of"]))
            bars = [{"date": b.date.isoformat(), "close": b.close} for b in series]
    except Exception:
        bars = None
    return replay_html.write(deps.out_dir, run.journal.events, bars)


def _outcome_line(outcome: Outcome) -> str:
    if outcome.status == "delivered":
        return (f"结果：delivered（原因 {outcome.reason}）；"
                f"判断：{outcome.judgment}；核验：{outcome.facts_and_rules}")
    return (f"结果：REVIEW（原因：{outcome.reason}）；"
            f"核验：{outcome.facts_and_rules}；判断：{outcome.judgment}")


def _print_process(run: Run) -> None:
    """过程：把日志里的事件翻译成几行中文。完整的时间线在回放与网页里。"""
    for event in run.journal.events:
        data = event.data
        if event.type == "run.started":
            request = data.get("request", {})
            print(f"· 开始：{request.get('ticker', '（演示）')} @ {request.get('as_of', '最近交易日')}"
                  f"（模式：{data.get('mode')}）")
        elif event.type == "model.called":
            usage = data.get("usage", {})
            print(f"· {event.actor} ← 模型 {data.get('model')}"
                  f"（{usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)} token）")
        elif event.type == "model.failed":
            print(f"· {event.actor} 的模型调用失败")
        elif event.type == "gate.checked":
            gate = data.get("gate") or ""
            findings = data.get("findings", 0)
            print(f"· 闸门 {_GATE.get(gate, gate)}：" + (f"发现 {findings} 个问题" if findings else "通过"))
        elif event.type == "route":
            print(f"· 打回：重跑 {data.get('to')}（{data.get('why')}）")
        elif event.type == "budget.exceeded":
            print(f"· 预算超限：{data.get('scope')}")
        elif event.type == "run.finished":
            print(f"· 结束：{data.get('status')}（{data.get('reason')}）")


@app.command()
def facts(
    ticker: Annotated[str, typer.Argument(help="代码，如 ACME")],
    date: Annotated[str, typer.Option("--date", help="分析日（ISO）；不给则用最后一根已收盘K线")] = "",
    universe: Annotated[str, typer.Option("--universe", help="数据集名字，默认取设置文件")] = "",
) -> None:
    """打印一个代码的事实账本与数据卡。不调用模型。"""
    overrides: dict[str, object] = {"run": {"universe": universe}} if universe else {}
    settings = load(ROOT, **overrides)
    deps = build_deps(settings)
    try:
        name = symbols.normalize(ticker)
        instrument = deps.market.instrument(name)
        as_of = date_type.fromisoformat(date) if date else deps.market.history(name, deps.today)[-1].date
        bars = deps.market.history(name, as_of)
        ledger, card = build_ledger(instrument, bars, as_of, deps.indicators,
                                    deps.market.dropped_rows(name))
        dis = disagreements(ledger, recompute_ledger(instrument, bars, as_of, deps.indicators))
    except (InvalidTicker, UnknownTicker, NoData, SourceUnavailable) as e:
        print(f"无法建立账本：{e}")
        raise typer.Exit(1) from e
    print(f"{instrument.ticker} {instrument.name}（{instrument.currency}，{instrument.market}）"
          f"分析日 {as_of}，账本用 {len(bars)} 根K线")
    for fact in ledger:
        unit = f" {fact.unit}" if fact.unit and fact.unit != "%" else ("%" if fact.unit == "%" else "")
        print(f"  {fact.id:<16} {fact.label:<24} {fact.value:<14g}{unit:<6} "
              f"@{fact.as_of}  bars={fact.bars_used}  {fact.source}")
    gaps = "；".join(card.gaps) or "无"
    unavailable = "；".join(f"{u.id}（{u.reason}）" for u in card.unavailable) or "无"
    warnings = "；".join(card.warnings) or "无"
    print(f"数据卡：首根 {card.first_bar}，末根 {card.last_bar}，共 {card.bars} 根，"
          f"距分析日 {card.stale_days} 天，丢弃 {card.dropped_rows} 行")
    print(f"缺口：{gaps}")
    print(f"不可用：{unavailable}")
    print(f"警告：{warnings}")
    print(f"复算分歧：{'、'.join(dis) if dis else '无'}")


@app.command()
def replay(
    run_dir: Annotated[str, typer.Argument(help="运行目录，如 runs/20260101-120000-ACME-…-abcd")],
) -> None:
    """按日志无模型重放一次运行，核对结果与黑板是否与当时一致。"""
    raise typer.Exit(replay_cmd.run(load(ROOT), run_dir))


@app.command()
def scenarios(
    name: Annotated[str, typer.Argument(help="只跑这个名字的场景；不给则跑全部")] = "",
) -> None:
    """跑离线场景，逐个打印预期与实际。任何一处不符都以退出码 1 结束。"""
    settings = load(ROOT)
    results = asyncio.run(run_all(partial(open_run, settings), run_flow, [name] if name else None))
    for result in results:
        print(summary_line(result))
    if not all(r.ok for r in results):
        raise typer.Exit(1)


@app.command()
def check() -> None:
    """打印配置（不含密钥），并探测配置的模型接受哪些输出方式。"""
    raise typer.Exit(check_cmd.run(load(ROOT)))


@app.command()
def fetch(
    tickers: Annotated[list[str], typer.Argument(help="一个或多个代码")],
    years: Annotated[int, typer.Option("--years", help="下载几年的日线")] = 3,
) -> None:
    """下载真实行情到 data/market/real/（连同各自的基准指数）。"""
    raise typer.Exit(fetch_cmd.run(load(ROOT), tuple(tickers), years))


@app.command()
def eval(
    name: Annotated[str, typer.Argument(help="inject、consistency、pit、ablation，或 all")],
) -> None:
    """运行离线评测。all 跑其中不需要模型的三项。"""
    raise typer.Exit(eval_cmd.run(ROOT, name))


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8765,
    no_open: Annotated[bool, typer.Option("--no-open", help="不打开浏览器")] = False,
) -> None:
    """启动本地网页：表单开运行、事件流看过程、列表看过去的运行。只监听 127.0.0.1。"""
    server_cmd.serve(load(ROOT), port, not no_open)


if __name__ == "__main__":
    app()
