"""命令行。每个命令读设置、调用下层、打印结果；跑流程的命令用 asyncio.run。

用户敲错的东西（设置文件里的未知键、无法解析的日期、不存在的场景）在这里变成一行中文说明
和退出码 2，不是一段回溯。回溯只留给真正的缺陷。

check、replay、eval、fetch 四个命令各调用自己模块里的一个函数（BLUEPRINT 10.3）。
选项用 Annotated 写法：默认值保持字面量，Typer 的说明放在注解里。
"""
from __future__ import annotations

import asyncio
import tempfile
import webbrowser
from datetime import date as date_type
from functools import partial
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ..adapters.scripted import Script
from ..domain import symbols
from ..domain.ledger import build_ledger, disagreements, recompute_ledger
from ..domain.market import Instrument, InvalidTicker, NoData, SourceUnavailable, UnknownTicker
from ..domain.types import DataCard, Fact, Item, Outcome, Scope
from ..evals import run as eval_cmd
from ..evals.scenarios import names as scenario_names
from ..evals.scenarios import run_all, summary_line
from ..flows import stages as flow_stages
from ..flows.analyze import analyze as run_flow
from ..flows.deps import Deps
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Event, Journal
from ..kernel.runtime import Run
from . import check as check_cmd
from . import fetch as fetch_cmd
from . import replay as replay_cmd
from . import server as server_cmd
from .settings import Settings, load
from .wiring import ROOT, BadRequest, Job, build_deps, describe_models, open_run, prepare, write_page

app = typer.Typer(help="InsightCrew：带独立审计的多智能体交易辅助系统。没有密钥时运行离线演示。",
                  no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)

_GATES = {"intake": "范围", "ledger": "账本", "evidence": "证据", "audit": "审计"}
_STAGES = {"technical": "技术分析", "fundamentals": "基本面分析", "disclosures": "公告分析",
           "debate": "多空辩论", "manager": "研究主管", "trader": "交易员"}
_CHANNEL_STATE = {"available": "可用", "empty": "窗口内没有公告", "unavailable": "不可用"}


def _fail(message: str, code: int = 2) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code)


def _settings(**flags: Any) -> Settings:
    """读设置。设置文件或环境变量写错时，说出是哪一个键，然后以退出码 2 结束。"""
    try:
        return load(ROOT, **flags)
    except (ValueError, OSError) as e:          # pydantic 的校验错误与 TOML 的语法错误都是 ValueError
        _fail(f"设置无效：{e}")


def _universe(name: str) -> dict[str, Any]:
    return {"run": {"universe": name}} if name else {}


# ---- analyze ----

@app.command()
def analyze(
    ticker: Annotated[str, typer.Argument(help="代码，如 ACME 或 0700.HK；不给则运行演示场景")] = "",
    date: Annotated[str, typer.Option("--date", help="分析日（YYYY-MM-DD）；不给则用最后一根已收盘 K 线")] = "",
    scenario: Annotated[str, typer.Option("--scenario", help="用预先写好的回复跑一个场景")] = "",
    universe: Annotated[str, typer.Option("--universe", help="数据集名字，默认取设置文件")] = "",
    channels: Annotated[str, typer.Option(
        "--channels", help="打开的证据通道，逗号分隔：fundamentals、disclosures")] = "",
    no_judges: Annotated[bool, typer.Option("--no-judges", help="跳过模型评审")] = False,
    debate_rounds: Annotated[int | None, typer.Option("--debate-rounds", min=0, help="辩论轮数")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="只打印结果，不打印过程")] = False,
    no_open: Annotated[bool, typer.Option("--no-open", help="不打开运行页面")] = False,
) -> None:
    """跑一次分析：给出评级与交易方案，或者说明为什么只能 REVIEW。"""
    settings = _settings(**_universe(universe))
    names = tuple(c.strip() for c in channels.split(",") if c.strip()) if channels else None
    try:
        job = prepare(settings, ticker=ticker, as_of=date, scenario=scenario,
                      judges=False if no_judges else None, debate_rounds=debate_rounds,
                      channels=names)
        run, deps = open_run(settings, job.request, scenario=job.scenario, flow=job.flow)
    except BadRequest as e:
        _fail(str(e))
    except OSError as e:
        _fail(f"数据集 {settings.run.universe!r} 读不出来：{e}")
    if names and job.scenario:
        print("场景运行以场景文件里写的通道为准，--channels 与设置里的 channels 都被忽略。")

    if not scenario:                              # 回退到离线从不静默：说一次原因（原因里没有密钥）
        mode, _, reason = describe_models(settings)
        if mode != "model":
            print(f"未使用真实模型：{reason}。" + ("改为运行离线演示场景 demo。" if job.scenario
                                           else "没有场景可依，需要模型的步骤会以 REVIEW 结束。"))

    outcome = asyncio.run(_run(run, deps, job, show=not quiet))
    page = write_page(run, deps)
    print(_outcome_line(outcome))
    print(f"运行目录：{deps.out_dir}")
    if outcome.reason == "model_unavailable":
        print(f"没有可用的模型。可以用 --scenario 跑离线场景：{'、'.join(scenario_names())}")
    if page is not None and not no_open:
        webbrowser.open(page.as_uri())


async def _run(run: Run, deps: Deps, job: Job, *, show: bool) -> Outcome:
    """跑流程，同时把新出现的事件逐行打印出来：真实模型的运行要一分钟上下，过程应当边跑边看得到。"""
    task = asyncio.create_task(run_flow(run, job.request, deps, job.flow))
    shown = 0
    while True:
        finished = task.done()
        events = run.journal.events
        if show:
            for event in events[shown:]:
                if line := _progress(event):
                    print(line)
        shown = len(events)
        if finished:
            return task.result()
        await asyncio.wait({task}, timeout=0.2)


def _progress(event: Event) -> str | None:
    """一条事件的一行中文。写黑板的事件不打印：完整的时间线在运行页面里。"""
    data = event.data
    if event.type == "run.started":
        request = data.get("request", {})
        return f"· 开始：{request.get('ticker')} @ {request.get('as_of', '最近交易日')}（模式：{data.get('mode')}）"
    if event.type == "model.called":
        usage = data.get("usage", {})
        retried = "，含一次纠正重试" if usage.get("requests", 1) > 1 else ""
        invalid = "，输出不合契约" if data.get("output") is None else ""
        return (f"· {event.step} ← {data.get('model')}"
                f"（{usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)} token{retried}{invalid}）")
    if event.type == "model.failed":
        return f"· {event.step} 的模型调用失败"
    if event.type == "gate.checked":
        gate = _GATES.get(data.get("gate", ""), data.get("gate", ""))
        if not data.get("findings"):
            return f"· 闸门 {gate}：通过"
        checks = "、".join(item.get("check", "") for item in data.get("detail") or ())
        return f"· 闸门 {gate}：发现 {data['findings']} 个问题（{checks}）"
    if event.type == "route":
        return f"· 打回：从 {_STAGES.get(data.get('to', ''), data.get('to'))} 重跑（{data.get('why')}）"
    if event.type == "budget.exceeded":
        return f"· 预算超限：{data.get('scope')}"
    if event.type == "run.finished":
        return f"· 结束：{data.get('status')}（{data.get('reason')}）"
    return None


def _outcome_line(outcome: Outcome) -> str:
    if outcome.status == "delivered":
        return (f"结果：delivered（原因 {outcome.reason}）；"
                f"判断：{outcome.judgment}；核验：{outcome.facts_and_rules}")
    return (f"结果：REVIEW（原因：{outcome.reason}）；"
            f"核验：{outcome.facts_and_rules}；判断：{outcome.judgment}")


# ---- facts ----

@app.command()
def facts(
    ticker: Annotated[str, typer.Argument(help="代码，如 ACME")],
    date: Annotated[str, typer.Option("--date", help="分析日（YYYY-MM-DD）；不给则用最后一根已收盘 K 线")] = "",
    universe: Annotated[str, typer.Option("--universe", help="数据集名字，默认取设置文件")] = "",
) -> None:
    """打印一个代码的事实账本与数据卡。不调用模型。"""
    settings = _settings(**_universe(universe))
    try:
        as_of = date_type.fromisoformat(date) if date else None
    except ValueError:
        _fail(f"日期 {date!r} 无法解析：请写成 YYYY-MM-DD")
    try:
        deps = build_deps(settings)
        name = symbols.normalize(ticker)
        instrument = deps.market.instrument(name)
        bars = deps.market.history(name, as_of or deps.today)
        as_of = as_of or bars[-1].date
        ledger, card = build_ledger(instrument, bars, as_of, deps.indicators,
                                    deps.market.dropped_rows(name))
        differing = disagreements(ledger, recompute_ledger(instrument, bars, as_of, deps.indicators))
    except (InvalidTicker, UnknownTicker, NoData, SourceUnavailable, OSError) as e:
        _fail(f"无法建立账本：{e}", code=1)

    print(f"{instrument.ticker} {instrument.name}（{instrument.currency}，{instrument.market}）"
          f"分析日 {as_of}，账本用 {len(bars)} 根 K 线")
    for fact in ledger:
        unit = "%" if fact.unit == "%" else f" {fact.unit}" if fact.unit else ""
        print(f"  {fact.id:<16} {fact.label:<24} {fact.value:<14g}{unit:<6} "
              f"@{fact.as_of}  bars={fact.bars_used}  {fact.source}")
    print(f"数据卡：首根 {card.first_bar}，末根 {card.last_bar}，共 {card.bars} 根，"
          f"距分析日 {card.stale_days} 天，丢弃 {card.dropped_rows} 行")
    print(f"缺口：{'；'.join(card.gaps) or '无'}")
    print(f"不可用：{'；'.join(f'{u.id}（{u.reason}）' for u in card.unavailable) or '无'}")
    print(f"警告：{'；'.join(card.warnings) or '无'}")
    print(f"复算分歧：{'、'.join(differing) or '无'}")
    _print_channels(deps, instrument, ledger, card, as_of)


def _print_channels(deps: Deps, instrument: Instrument, ledger: list[Fact],
                    card: DataCard, as_of: date_type) -> None:
    """两个证据通道在这次分析日会读到什么。数字与运行里的 evidence 阶段完全一致：
    这里用同一阶段在一张临时黑板上跑一遍（不调模型），场景文件的数值从本命令的输出抄写。"""
    journal = Journal()
    probe = Run(id="facts", journal=journal, board=Board(journal),
                models=Script({}), budget=Budget())
    probe.board.put("request/scope",
                    Scope(ticker=instrument.ticker, as_of=as_of.isoformat(),
                          universe=deps.universe, requested=instrument.ticker), by="facts")
    probe.board.put("ledger/instrument", instrument, by="facts")
    probe.board.put("ledger/facts", ledger, by="facts")
    probe.board.put("ledger/card", card, by="facts")
    flow_stages.evidence(probe, deps, ("fundamentals", "disclosures"))
    board = probe.board
    if board.has("fundamentals/status"):
        status = board.get("fundamentals/status")["state"]
        print(f"基本面通道：{_CHANNEL_STATE[status]}"
              + (f"（{board.get('fundamentals/status')['reason']}）" if status != "available" else ""))
        if status == "available":
            for fact in board.get("fundamentals/facts", list[Fact]):
                unit = "%" if fact.unit == "%" else f" {fact.unit}" if fact.unit else ""
                print(f"  {fact.id:<20} {fact.label:<28} {fact.value:<14g}{unit:<8} "
                      f"@{fact.as_of}  {fact.source}")
            fund_card = board.get("fundamentals/card")
            print(f"  数据卡：来源 {fund_card['source']}，货币 {fund_card['currency']}，"
                  f"最近财年末 {fund_card['fiscal_year_end']}，最晚申报 {fund_card['filed']}，"
                  f"{fund_card['rows']} 行")
            if fund_card["unavailable"]:
                missing = "；".join(f"{u['id']}（{u['reason']}）" for u in fund_card["unavailable"])
                print(f"  不可用：{missing}")
            if fund_card["warnings"]:
                print(f"  重述提醒：{'；'.join(fund_card['warnings'])}")
    if board.has("disclosures/status"):
        status = board.get("disclosures/status")["state"]
        print(f"公告通道：{_CHANNEL_STATE[status]}"
              + (f"（{board.get('disclosures/status')['reason']}）" if status != "available" else ""))
        if status == "available":
            for item in board.get("disclosures/items", list[Item]):
                print(f"  {item.id:<4} {item.published}  {item.kind}: {item.title}"
                      f"（{item.ref}）")
            dcard = board.get("disclosures/card")
            print(f"  窗口 {dcard['window_from']} 到 {dcard['window_to']}，{dcard['items']} 条，"
                  f"丢弃无日期 {dcard['dropped']} 条，截断 {dcard['truncated']} 条")


# ---- scenarios ----

@app.command()
def scenarios(
    name: Annotated[str, typer.Argument(help="只跑这个名字的场景；不给则跑全部")] = "",
) -> None:
    """跑离线场景，逐个打印预期与实际。任何一处不符都以退出码 1 结束。"""
    settings = _settings()
    if name and name not in scenario_names():
        _fail(f"没有名为 {name!r} 的场景。可用的场景：{'、'.join(scenario_names())}")
    # 场景在这里是一次检查，不是一次要留档的运行：跑在临时目录里，不进 runs/。
    # 想留下某个场景的日志与页面，用 analyze --scenario NAME。
    with tempfile.TemporaryDirectory() as scratch:
        opener = partial(open_run, settings, runs_dir=Path(scratch))
        results = asyncio.run(run_all(opener, run_flow, [name] if name else None))
    for result in results:
        print(summary_line(result))
    if not all(result.ok for result in results):
        raise typer.Exit(1)


# ---- 各有自己模块的命令 ----

@app.command()
def replay(
    run_dir: Annotated[str, typer.Argument(help="运行目录，如 runs/20260101-120000-ACME-…-abcd")],
) -> None:
    """按日志无模型重放一次运行，核对结果与黑板是否与当时一致。"""
    raise typer.Exit(replay_cmd.run(_settings(), run_dir))


@app.command()
def check() -> None:
    """打印配置（不含密钥），并探测配置的模型接受哪些输出方式。"""
    raise typer.Exit(check_cmd.run(_settings()))


@app.command()
def fetch(
    tickers: Annotated[list[str], typer.Argument(help="一个或多个代码")],
    years: Annotated[int, typer.Option("--years", min=1, help="下载几年的日线")] = 3,
) -> None:
    """下载真实行情到 data/market/real/（连同各自的基准指数）。"""
    raise typer.Exit(fetch_cmd.run(_settings(), tuple(tickers), years))


@app.command(name="eval")
def evaluate(
    name: Annotated[str, typer.Argument(help="inject、consistency、pit、ablation，或 all")],
) -> None:
    """运行评测。all 跑其中不需要模型的三项；ablation 需要真实模型。"""
    if name != "ablation":
        raise typer.Exit(eval_cmd.run(ROOT, name))
    settings = _settings()
    mode, _, reason = describe_models(settings)
    if mode != "model":
        raise typer.Exit(eval_cmd.run(ROOT, name, no_model_reason=reason))
    raise typer.Exit(eval_cmd.run(ROOT, name, open_run=partial(open_run, settings), analyze=run_flow,
                                  base=settings.flow))


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="监听端口")] = 8765,
    no_open: Annotated[bool, typer.Option("--no-open", help="不打开浏览器")] = False,
) -> None:
    """启动本地网页：表单开运行、事件流看过程、列表看过去的运行。只监听 127.0.0.1。"""
    try:
        server_cmd.serve(_settings(), port, not no_open)
    except OSError as e:
        _fail(f"本地服务无法启动：{e}", code=1)


if __name__ == "__main__":
    app()
