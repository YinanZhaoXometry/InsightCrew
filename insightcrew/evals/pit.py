"""时点评测（蓝图 12.3 的 `pit`，18.13 加了证据通道块，19.16 加了版本 2.2 块）。

行情块：加新 K 线不改变任何事实，也没有事实晚于分析日。
通道块（18.13）：加更晚的申报与更晚的公告，不改变任何更早日期的基本面事实与条目列表；
没有基本面事实的申报日晚于分析日，也没有条目发布晚于分析日。
版本 2.2 块（19.16）：新闻与社交条目晚于分析日绝不通过端口；宏观只用不晚于分析日的
版本（沿 cpi 重述前后扫掠）；预测市场只在 fetched_on 恰为分析日那天可读；教训绝不
选中 known_on 晚于分析日的结果；工具事实只用分析日（含）之前的 K 线（漏 bar 检查）。
任何一处违规退出码 1，一处违规都没有才退出 0。
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..adapters.declarations import load_fundamentals, load_indicators
from ..adapters.declarations_macro import load_macro
from ..adapters.declarations_tools import load_tool_indicators
from ..adapters.disclosures_json import JsonDisclosures
from ..adapters.macro_json import JsonMacro
from ..adapters.market_csv import CsvMarket
from ..adapters.news_json import JsonNews
from ..adapters.scripted import Script
from ..adapters.social_json import JsonSocial
from ..adapters.statements_json import JsonStatements
from ..domain.fundamentals_ledger import build_fundamentals
from ..domain.ledger import build_ledger
from ..domain.macro import build_macro, value_at
from ..domain.market import Bar, Instrument, NotCovered
from ..domain.memory import DecisionRecord, OutcomeRecord, select_lessons
from ..domain.news import NewsSet
from ..domain.social import SocialSet
from ..domain.types import Action, Fact, Rating, Scope
from ..flows.deps import Deps
from ..flows.tools import build_tools
from ..kernel.board import Board
from ..kernel.budget import Budget
from ..kernel.events import Journal
from ..kernel.runtime import Halt, Run

_UNIVERSE = "synthetic"
_FAR_FUTURE = datetime.date(2099, 1, 1)   # 取整段序列用：晚于任何数据日期
_TICKERS = ("ACME", "0000.HK", "GAPPY")   # 蓝图点名的三个标的
_DATES = 6                                # 每个标的的分析日个数（含首末两个交易日）
_WINDOW_DAYS = 90                         # 公告块的窗口长度，与运行的默认设置一致

# ---- 版本 2.2 块的常量：合成档案只在 ACME 身上，宏观沿重述日扫掠 ----

_TICKER = "ACME"                          # 新闻/社交/工具块用的标的（合成档案的主人）
_EVIDENCE_WINDOW = 7                      # 新闻与社交的窗口长度，与 [news]/[sentiment] 默认一致
_COVER_TO = datetime.date(2026, 12, 31)   # 新闻/社交档案声明的 covers_to
_NEWS_DATES = (*(datetime.date(2026, 9, d) for d in (18, 23, 24, 25, 27, 30)),
               datetime.date(2026, 10, 2), datetime.date(2026, 10, 3), _COVER_TO)
# 宏观：月末扫掠 + cpi 两次重述（2026-01 观测在 2026-03-12 改版，2026-08 观测在
# 2026-10-13 改版；WP28 记录的边界数字 329.4/329.5 与 337.0/337.2）。
_MACRO_MONTH_ENDS = (datetime.date(2025, 3, 31), datetime.date(2025, 6, 30),
                     datetime.date(2025, 12, 31), datetime.date(2026, 3, 11),
                     datetime.date(2026, 3, 12), datetime.date(2026, 6, 30),
                     datetime.date(2026, 9, 30))
_CPI_JAN = datetime.date(2026, 1, 31)     # cpi 2026-01 观测所在月末（观测期记在月首）
_CPI_AUG = datetime.date(2026, 8, 31)
_TOOL_DATES = (datetime.date(2026, 3, 31), datetime.date(2026, 6, 30),
               datetime.date(2026, 9, 30))   # 工具块的分析日：早、中、账本最后一根
_MARKET_FETCH_DAYS = (datetime.date(2026, 9, 29), datetime.date(2026, 9, 30))
_MARKET_SWEEP = (*(datetime.date(2026, 9, d) for d in range(27, 31)),
                 datetime.date(2026, 10, 1), datetime.date(2026, 10, 2))


def _analysis_dates(full: tuple[Bar, ...]) -> list[datetime.date]:
    """一段序列里均匀取 _DATES 个分析日（都是序列自己的交易日，与 test_ledger 同一取法）。"""
    step = (len(full) - 1) / (_DATES - 1)
    return [full[round(i * step)].date for i in range(_DATES)]


def _violations(ticker: str, as_of: datetime.date, from_history: list[Fact],
                from_cut: list[Fact]) -> list[str]:
    """同一点上的违规：事实集合不同、加入后期 K 线后取值改变、或 as_of 晚于分析日。"""
    point = as_of.isoformat()
    problems: list[str] = []
    history_values = {fact.id: fact.value for fact in from_history}
    cut_values = {fact.id: fact.value for fact in from_cut}
    if tuple(history_values) != tuple(cut_values):
        problems.append(f"{ticker} @ {point}：两份账本的事实集合不同")
    else:
        for fact_id, value in history_values.items():
            if value != cut_values[fact_id]:
                problems.append(f"{ticker} @ {point}：加入后期 K 线后事实 {fact_id} 的取值改变"
                                f"（{value} → {cut_values[fact_id]}）")
    for fact in (*from_history, *from_cut):
        if datetime.date.fromisoformat(fact.as_of) > as_of:
            problems.append(f"{ticker} @ {point}：事实 {fact.id} 的 as_of {fact.as_of}"
                            f" 晚于分析日 {point}")
    return problems


def main(root: Path) -> int:
    """跑时点评测：无违规打印一行结论并退出 0，有违规先逐条列出再退出 1。"""
    specs = load_indicators(root / "data" / "indicators.toml")
    market = CsvMarket(root / "data" / "market", _UNIVERSE)
    points = 0
    compared = 0
    problems: list[str] = []
    for ticker in _TICKERS:
        instrument = market.instrument(ticker)
        dropped = market.dropped_rows(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _analysis_dates(full):
            from_history, _card = build_ledger(instrument, market.history(ticker, as_of), as_of,
                                               specs, dropped)
            cut = tuple(bar for bar in full if bar.date <= as_of)   # 手工掐到分析日（含当天）
            from_cut, _card = build_ledger(instrument, cut, as_of, specs, dropped)
            points += 1
            compared += len(from_history) + len(from_cut)
            problems.extend(_violations(ticker, as_of, from_history, from_cut))
    for line in problems:
        print(f"违规：{line}")
    verdict = ("加新 K 线不改变任何事实，也没有事实晚于分析日" if not problems
               else f"{len(problems)} 处违规")
    print(f"时点评测：{points} 个数据点，比对 {compared} 项事实：{verdict}")
    _channel_block(root, market, problems)
    news = JsonNews(root / "data" / "market", _UNIVERSE)
    social = JsonSocial(root / "data" / "market", _UNIVERSE)
    _window_block("新闻", news.headlines,
                  lambda s: tuple((h.published, h.title) for h in s.items), problems)
    _window_block("社交", social.posts,
                  lambda s: tuple((p.published, p.text) for p in s.posts), problems)
    _macro_block(root, problems)
    _markets_block(problems)
    _lessons_block(problems)
    _tool_facts_block(root, market, problems)
    return 0 if not problems else 1


def _channel_block(root: Path, market: CsvMarket, problems: list[str]) -> int:
    """通道块（18.13）：更晚的申报与更晚的公告，不改变更早日期的基本面事实与条目列表。

    无年报数据的标的（端口以 NotCovered 回答）自然不贡献点。
    """
    specs = load_fundamentals(root / "data" / "fundamentals.toml")
    statements = JsonStatements(root / "data" / "market", _UNIVERSE)
    disclosures = JsonDisclosures(root / "data" / "market", _UNIVERSE)
    points = 0
    for ticker in _TICKERS:
        instrument = market.instrument(ticker)
        full = market.history(ticker, _FAR_FUTURE)
        for as_of in _analysis_dates(full):
            close = market.history(ticker, as_of)[-1].close
            point = as_of.isoformat()
            try:
                ss = statements.statements(ticker, as_of)
                later = statements.statements(ticker, full[-1].date)
            except NotCovered:                          # 没有年报数据：跳过基本面部分
                pass
            else:
                later_cut = dataclasses.replace(
                    later, rows=tuple(row for row in later.rows if row.filed <= as_of))
                facts_now = build_fundamentals(instrument, ss, close, as_of, as_of, specs)[0]
                now = {f.id: f.value for f in facts_now}
                later_facts = {f.id: f.value for f in build_fundamentals(
                    instrument, later_cut, close, as_of, as_of, specs)[0]}
                points += 1
                if tuple(now) != tuple(later_facts):
                    problems.append(f"{ticker} 基本面 @ {point}：加入更晚的申报后事实集合改变")
                else:
                    for fact_id, value in now.items():
                        if value != later_facts[fact_id]:
                            problems.append(f"{ticker} 基本面 @ {point}：加入更晚的申报后"
                                            f"事实 {fact_id} 的取值改变")
                for fact in facts_now:
                    if datetime.date.fromisoformat(fact.as_of) > as_of:
                        problems.append(f"{ticker} 基本面 @ {point}：事实 {fact.id} 的 as_of"
                                        f" {fact.as_of} 晚于分析日")
            try:
                window = disclosures.published(ticker, as_of - datetime.timedelta(days=_WINDOW_DAYS),
                                               as_of)
                later_items = disclosures.published(
                    ticker, as_of - datetime.timedelta(days=_WINDOW_DAYS), full[-1].date)
                later_cut_items = tuple(p for p in later_items.items if p.published <= as_of)
            except NotCovered:
                continue
            points += 1
            if tuple(window.items) != later_cut_items:
                problems.append(f"{ticker} 公告 @ {point}：加入更晚的公告后窗口条目列表改变")
    for line in problems:
        if "基本面 @" in line or "公告 @" in line:
            print(f"违规：{line}")
    bad = [p for p in problems if "基本面 @" in p or "公告 @" in p]
    verdict = ("加更晚的申报与公告不改变任何更早日期的事实或条目，也没有事实晚于分析日"
               if not bad else f"{len(bad)} 处违规")
    print(f"证据通道时点评测：{points} 个数据点：{verdict}")
    return points


# ---- 版本 2.2 块（BLUEPRINT 19.16）：新闻/社交、宏观、预测市场、教训、工具事实 ----


def _window_block[WindowSet: (NewsSet, SocialSet)](
        label: str,
        read: Callable[[str, datetime.date, datetime.date], WindowSet],
        rows: Callable[[WindowSet], tuple[tuple[datetime.date, str], ...]],
        problems: list[str]) -> int:
    """新闻与社交（19.8 规则 2）：条目发布晚于分析日绝不通过端口，更晚的条目不改变更早的窗口。

    在合成档案（ACME，覆盖到 2026-12-31，其中带着分析日之后的条目）身上，
    沿各条目的发布日扫掠：每个分析日读一次窗口，再读同一窗口起点到档案尽头的条目、
    手工掐到分析日，两者必须相同，且没有任何一条发布晚于分析日。
    """
    points = 0
    for as_of in _NEWS_DATES:
        start = as_of - datetime.timedelta(days=_EVIDENCE_WINDOW)
        try:
            now = read(_TICKER, start, as_of)
            later = read(_TICKER, start, _COVER_TO)
        except NotCovered:                      # 没有档案的标的不贡献点（ACME 之外）
            continue
        points += 1
        point = as_of.isoformat()
        rows_now = rows(now)
        rows_cut = tuple(row for row in rows(later) if row[0] <= as_of)
        if rows_now != rows_cut:
            problems.append(f"{label} @ {point}：加入更晚的条目后窗口列表改变")
        for published, _text in rows_now:
            if published > as_of:
                problems.append(f"{label} @ {point}：条目发布于 {published.isoformat()}，"
                                f"晚于分析日 {point}")
    bad = [p for p in problems if p.startswith(f"{label} @")]
    for line in bad:
        print(f"违规：{line}")
    verdict = ("晚于分析日的条目绝不通过端口，更晚的条目不改变更早的窗口"
               if not bad else f"{len(bad)} 处违规")
    print(f"{label}时点评测：{points} 个数据点：{verdict}")
    return points


def _macro_block(root: Path, problems: list[str]) -> int:
    """宏观（19.7 时点规则）：只用 realtime_start 不晚于分析日的版本，沿 cpi 重述扫掠。

    每个分析日检查三件事：端口交出的观测没有一条版本或观测期晚于分析日；
    build_macro 的每条事实 as_of（观测期）不晚于分析日；cpi 的两次重述在边界两侧
    读到 WP28 记录的旧值/新值（2026-03-12 前后 329.4/329.5，2026-10-13 前后 337.0/337.2）。
    """
    specs = load_macro(root / "data" / "macro.toml")
    port = JsonMacro(root / "data" / "market", _UNIVERSE)
    points = 0
    for as_of in _MACRO_MONTH_ENDS:
        for spec in specs:
            try:
                series = port.series(spec.alias, as_of)
            except NotCovered:                  # 分析日早于该序列最早的观测期
                continue
            point = as_of.isoformat()
            points += 1
            for obs in series.observations:
                if obs.realtime_start > as_of or obs.date > as_of:
                    problems.append(f"宏观 {spec.alias} @ {point}：端口交出观测"
                                    f"（观测期 {obs.date.isoformat()}，版本 "
                                    f"{obs.realtime_start.isoformat()}）晚于分析日")
            facts, _unavailable = build_macro([series], [spec], as_of)
            for fact in facts:
                if datetime.date.fromisoformat(fact.as_of) > as_of:
                    problems.append(f"宏观 {spec.alias} @ {point}：事实 {fact.id} 的 as_of"
                                    f" {fact.as_of} 晚于分析日")
    points += _revision_sweep(port, "cpi", _CPI_JAN, datetime.date(2026, 2, 12),
                              datetime.date(2026, 3, 13), datetime.date(2026, 3, 12),
                              329.4, 329.5, problems)
    points += _revision_sweep(port, "cpi", _CPI_AUG, datetime.date(2026, 9, 12),
                              datetime.date(2026, 10, 14), datetime.date(2026, 10, 13),
                              337.0, 337.2, problems)
    bad = [p for p in problems if p.startswith("宏观 ")]
    for line in bad:
        print(f"违规：{line}")
    verdict = ("晚于分析日的版本绝不被使用，重述只在版本生效日起读到新值"
               if not bad else f"{len(bad)} 处违规")
    print(f"宏观时点评测：{points} 个数据点：{verdict}")
    return points


def _revision_sweep(port: JsonMacro, alias: str, month_end: datetime.date,
                    first: datetime.date, last: datetime.date, flips_on: datetime.date,
                    before: float, after: float, problems: list[str]) -> int:
    """沿一次重述逐日扫掠：flips_on（含）之前读旧值，之后读新值（WP28 记录的边界数字）。"""
    points = 0
    day = first
    while day <= last:
        points += 1
        expected = before if day < flips_on else after
        value = value_at(port.series(alias, day).observations, month_end, day)
        if value != expected:
            problems.append(f"宏观 {alias} @ {day.isoformat()}：重述边界读数应为 {expected}，"
                            f"实际 {value}（重述版 {flips_on.isoformat()} 生效）")
        day += datetime.timedelta(days=1)
    return points


def _markets_block(problems: list[str]) -> int:
    """预测市场（19.8 规则 3）：只有 fetched_on 恰为分析日的那一份档案可读，其余日期一律读不到。

    预测市场只有当下的报价、没有历史（决策 47），仓库里没有合成档案；这里在临时目录
    写两份确定性的合成档案（两个抓取日各一份），再沿抓取日前后扫掠。读取规则按 19.8
    原文就地实现（文件名即分析日，且 fetched_on 必须等于分析日），与 flows/stage_news.py
    的 _markets 同一条规则：那是私有函数且只读仓库的数据目录，而本评测要读的是自己
    临时目录里的档案，故各持一份，规则改变时两处一起改。
    """
    with tempfile.TemporaryDirectory() as tmp:
        markets_dir = Path(tmp) / "markets"
        markets_dir.mkdir()
        for day in _MARKET_FETCH_DAYS:
            archive = {"source": "合成数据", "fetched_on": day.isoformat(),
                       "markets": [{"question": f"美联储在 {day.isoformat()} 之后是否按兵不动",
                                    "probability": 0.62, "volume": 125000.0,
                                    "end_date": "2026-12-31"}]}
            (markets_dir / f"{day.isoformat()}.json").write_text(
                json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        points = 0
        for as_of in _MARKET_SWEEP:
            points += 1
            rows = _markets_rows(markets_dir, as_of)
            if as_of in _MARKET_FETCH_DAYS and not rows:
                problems.append(f"预测市场 @ {as_of.isoformat()}：抓取日当天反而在档案里"
                                f"读不到任何行情")
            if as_of not in _MARKET_FETCH_DAYS and rows:
                problems.append(f"预测市场 @ {as_of.isoformat()}：不是抓取日却读到了行情"
                                f"（fetched_on 必须恰为分析日）")
    bad = [p for p in problems if p.startswith("预测市场 ")]
    for line in bad:
        print(f"违规：{line}")
    verdict = ("只有 fetched_on 恰为分析日的那天读得到行情" if not bad else f"{len(bad)} 处违规")
    print(f"预测市场时点评测：{points} 个数据点：{verdict}")
    return points


def _markets_rows(markets_dir: Path, as_of: datetime.date) -> list[dict[str, object]]:
    """19.8 的读取规则：只有 fetched_on 恰为分析日的那一份档案可用，其余日期一律为空。"""
    path = markets_dir / f"{as_of.isoformat()}.json"
    if not path.exists():
        return []
    archive = json.loads(path.read_text(encoding="utf-8"))
    if archive.get("fetched_on") != as_of.isoformat():
        return []
    return list(archive.get("markets", ()))


def _lesson_records() -> list[DecisionRecord | OutcomeRecord]:
    """三对决策/结果：known_on 分别在扫掠日之前、之间、之后，人人带教训与配对决策。"""
    return [
        DecisionRecord(kind="decision", run_id="r1", ticker="ACME", as_of="2026-09-10",
                       rating=Rating.BUY, action=Action.BUY, entry=220.0, stop=210.0,
                       target=240.0, size_pct=8.0, horizon_days=5),
        OutcomeRecord(kind="outcome", run_id="r1", ticker="ACME", as_of="2026-09-10",
                      known_on="2026-09-20", holding_days=5, return_pct=2.5,
                      benchmark="SYNUS", benchmark_return_pct=1.0, alpha_pct=1.5,
                      hit="target", lesson="趋势延续时让利润走完整个持有期，不要提前下车"),
        DecisionRecord(kind="decision", run_id="r2", ticker="ACME", as_of="2026-09-15",
                       rating=Rating.HOLD, action=Action.HOLD, entry=None, stop=None,
                       target=None, size_pct=0.0, horizon_days=5),
        OutcomeRecord(kind="outcome", run_id="r2", ticker="ACME", as_of="2026-09-15",
                      known_on="2026-10-05", holding_days=5, return_pct=-1.2,
                      benchmark="SYNUS", benchmark_return_pct=0.4, alpha_pct=-1.6,
                      hit="n/a", lesson="没有论点的观望也要复盘，空仓本身是一种判断"),
        DecisionRecord(kind="decision", run_id="r3", ticker="GAPPY", as_of="2026-09-08",
                       rating=Rating.SELL, action=Action.SELL, entry=None, stop=None,
                       target=None, size_pct=5.0, horizon_days=5),
        OutcomeRecord(kind="outcome", run_id="r3", ticker="GAPPY", as_of="2026-09-08",
                      known_on="2026-09-25", holding_days=5, return_pct=-3.1,
                      benchmark="SYNUS", benchmark_return_pct=1.0, alpha_pct=-4.1,
                      hit="stop", lesson="缺口频出的标的上止损要放得更远，避免被噪音扫出"),
    ]


# 各扫掠日应选中的教训（按 select_lessons 的口径：known_on 不晚于分析日才算已知）。
_LESSON_EXPECTED: dict[datetime.date, tuple[tuple[str, str], ...]] = {
    datetime.date(2026, 9, 19): (),
    datetime.date(2026, 9, 20): (("ACME", "2026-09-20"),),
    datetime.date(2026, 9, 25): (("ACME", "2026-09-20"), ("GAPPY", "2026-09-25")),
    datetime.date(2026, 9, 30): (("ACME", "2026-09-20"), ("GAPPY", "2026-09-25")),
    datetime.date(2026, 10, 5): (("ACME", "2026-10-05"), ("ACME", "2026-09-20"),
                                 ("GAPPY", "2026-09-25")),
}


def _lessons_block(problems: list[str]) -> int:
    """教训（不变量 20）：known_on 晚于分析日的结果绝不被选中，边界日恰好在那天可知。"""
    records = _lesson_records()
    points = 0
    for as_of, expected in _LESSON_EXPECTED.items():
        point = as_of.isoformat()
        lessons = select_lessons(records, "ACME", as_of)
        points += 1
        for entry in lessons:
            if datetime.date.fromisoformat(entry.known_on) > as_of:
                problems.append(f"教训 @ {point}：选中了 known_on {entry.known_on} 晚于分析日"
                                f"的结果（{entry.ticker}）")
        got = tuple((entry.ticker, entry.known_on) for entry in lessons)
        if got != expected:
            problems.append(f"教训 @ {point}：应选中 {expected}，实际 {got}")
    bad = [p for p in problems if p.startswith("教训 @")]
    for line in bad:
        print(f"违规：{line}")
    verdict = ("known_on 晚于分析日的结果绝不被选中" if not bad else f"{len(bad)} 处违规")
    print(f"教训时点评测：{points} 个数据点：{verdict}")
    return points


class _LeakyMarket:
    """坏端口：无视 as_of，把整段序列（含分析日之后的 bar）原样交出去（与 WP26 的测试同款）。"""

    def __init__(self, inner: CsvMarket):
        self.inner = inner

    def instrument(self, ticker: str) -> Instrument:
        return self.inner.instrument(ticker)

    def history(self, ticker: str, as_of: datetime.date) -> tuple[Bar, ...]:
        return self.inner.history(ticker, _FAR_FUTURE)

    def dropped_rows(self, ticker: str) -> int:
        return self.inner.dropped_rows(ticker)


def _tool_run(as_of: datetime.date) -> Run:
    """一个只带 request/scope 的运行：工具在 intake 之后、任何模型调用之前构建。"""
    journal = Journal()
    run = Run(id="pit", journal=journal, board=Board(journal), models=Script({}),
              budget=Budget())
    run.board.put("request/scope", Scope(ticker=_TICKER, as_of=as_of.isoformat(),
                                         universe=_UNIVERSE, requested=_TICKER), by="intake")
    return run


def _tool_facts_block(root: Path, market: CsvMarket, problems: list[str]) -> int:
    """工具事实（不变量 21）：只用分析日（含）之前的 K 线，坏端口多给的 bar 影响不了任何结果。

    每个分析日各建两套工具：一套接诚实的行情端口，一套接无视 as_of 的坏端口；
    同一家族同一窗口的答案必须相同（漏 bar 检查，与 WP26 的测试同一思路），
    且每条事实的 as_of 不晚于分析日。
    """
    specs = load_tool_indicators(root / "data" / "tool_indicators.toml")
    leaky = _LeakyMarket(market)
    points = 0
    for as_of in _TOOL_DATES:
        point = as_of.isoformat()
        honest = build_tools(_tool_run(as_of), Deps(market=market, indicators=(), rules=(),
                                                   today=as_of, universe=_UNIVERSE), specs)
        leaky_tools = build_tools(_tool_run(as_of), Deps(market=leaky, indicators=(), rules=(),
                                                        today=as_of, universe=_UNIVERSE), specs)
        for spec in specs:
            for n in sorted({spec.min, min(20, spec.max), spec.max}):
                points += 1
                try:
                    clean = honest["indicator"](spec.key, n)
                    dirty = leaky_tools["indicator"](spec.key, n)
                except Halt as e:               # 两条路径分歧：build_tools 与账本一样停机
                    problems.append(f"工具 {spec.key}_{n} @ {point}：两条计算路径分歧"
                                    f"（{e.reason}）")
                    continue
                if clean != dirty:
                    problems.append(f"工具 {spec.key}_{n} @ {point}：分析日之后的 bar 改变了结果")
                if isinstance(clean, dict) and clean["as_of"] > point:
                    problems.append(f"工具 {spec.key}_{n} @ {point}：事实 as_of {clean['as_of']}"
                                    f" 晚于分析日")
    bad = [p for p in problems if p.startswith("工具 ")]
    for line in bad:
        print(f"违规：{line}")
    verdict = ("工具只用分析日（含）之前的 K 线，多给的 bar 影响不了任何结果"
               if not bad else f"{len(bad)} 处违规")
    print(f"工具时点评测：{points} 个数据点：{verdict}")
    return points


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parent.parent.parent))
