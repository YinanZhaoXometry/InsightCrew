"""WP37 的时点评测测试：版本 2.2 的六个新块（BLUEPRINT 19.16）。

每块两问：合成数据上无违规（点数与结论行由 pit 自身打印）；伪造一处时点泄漏时
该块必须报出违规（漏 bar 的读取器、颠倒的重述边界、fetched_on 与文件名不一致的
档案、来自未来的教训、as_of 晚于分析日的工具事实）。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from insightcrew.adapters.macro_json import JsonMacro
from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.adapters.news_json import JsonNews
from insightcrew.adapters.social_json import JsonSocial
from insightcrew.domain.memory import LessonEntry
from insightcrew.domain.news import Headline, NewsSet
from insightcrew.domain.social import SocialSet
from insightcrew.evals import pit
from insightcrew.flows.tools import ToolBox

ROOT = Path(__file__).resolve().parent.parent
_MARKET_ROOT = ROOT / "data" / "market"


def _news_rows(s: NewsSet) -> tuple[tuple[datetime.date, str], ...]:
    return tuple((h.published, h.title) for h in s.items)


def _social_rows(s: SocialSet) -> tuple[tuple[datetime.date, str], ...]:
    return tuple((p.published, p.text) for p in s.posts)


# ---- 新闻与社交：条目晚于分析日绝不通过端口 ----


def test_the_news_and_social_blocks_pass_on_the_synthetic_archive() -> None:
    problems: list[str] = []
    news = pit._window_block("新闻", JsonNews(_MARKET_ROOT, "synthetic").headlines,
                             _news_rows, problems)
    social = pit._window_block("社交", JsonSocial(_MARKET_ROOT, "synthetic").posts,
                               _social_rows, problems)
    assert news == 9 and social == 9 and problems == []


def test_a_window_that_leaks_a_future_item_is_reported() -> None:
    """撒谎的读取器：窗口里混进一条发布晚于分析日的条目，必须逐日报违规。"""

    def leak(ticker: str, start: datetime.date, as_of: datetime.date) -> NewsSet:
        return NewsSet(source="合成数据", items=(
            Headline(published=datetime.date(2026, 10, 3), source="合成数据",
                     title="晚于分析日的条目", url="https://example.com/future"),), dropped=0)

    problems: list[str] = []
    pit._window_block("新闻", leak, _news_rows, problems)
    assert any("晚于分析日" in p for p in problems)


# ---- 宏观：晚于分析日的版本绝不被使用，重述只在生效日起读到新值 ----


def test_the_macro_block_passes_on_the_synthetic_archive() -> None:
    problems: list[str] = []
    assert pit._macro_block(ROOT, problems) == 91
    assert problems == []


def test_the_revision_sweep_reports_a_wrong_boundary() -> None:
    """边界期望值故意写错：扫掠必须逐日报出与档案不符的读数（档案这两天都读 329.4）。"""
    port = JsonMacro(_MARKET_ROOT, "synthetic")
    problems: list[str] = []
    points = pit._revision_sweep(port, "cpi", datetime.date(2026, 1, 31),
                                 datetime.date(2026, 2, 12), datetime.date(2026, 2, 13),
                                 datetime.date(2026, 2, 12), 999.0, 329.5, problems)
    assert points == 2 and len(problems) == 2


# ---- 预测市场：只有 fetched_on 恰为分析日的那一份档案可读 ----


def test_markets_rows_follow_the_fetched_on_rule(tmp_path: Path) -> None:
    markets = tmp_path / "markets"
    markets.mkdir()
    good = {"source": "合成数据", "fetched_on": "2026-09-30",
            "markets": [{"question": "按兵不动？", "probability": 0.62, "volume": 125000.0,
                         "end_date": "2026-12-31"}]}
    (markets / "2026-09-30.json").write_text(json.dumps(good), encoding="utf-8")
    mismatched = {"source": "合成数据", "fetched_on": "2026-09-29", "markets": good["markets"]}
    (markets / "2026-10-01.json").write_text(json.dumps(mismatched), encoding="utf-8")
    assert pit._markets_rows(markets, datetime.date(2026, 9, 30)) == good["markets"]
    assert pit._markets_rows(markets, datetime.date(2026, 10, 1)) == []   # 文件名与 fetched_on 不一致
    assert pit._markets_rows(markets, datetime.date(2026, 9, 29)) == []   # 那天没有档案


def test_the_markets_block_passes_on_its_two_fetch_days() -> None:
    problems: list[str] = []
    assert pit._markets_block(problems) == 6
    assert problems == []


# ---- 教训：known_on 晚于分析日的结果绝不被选中 ----


def test_the_lessons_block_passes_and_pins_the_boundary_days() -> None:
    problems: list[str] = []
    assert pit._lessons_block(problems) == 5
    assert problems == []


def test_a_lesson_from_the_future_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """被篡改的 select_lessons 交出 known_on 晚于分析日的教训：必须报违规。"""
    future = (LessonEntry(ticker="ACME", as_of="2026-09-10", known_on="2026-12-31",
                          rating="buy", return_pct=1.0, alpha_pct=None, hit="neither",
                          lesson="来自未来的教训"),)
    monkeypatch.setattr(pit, "select_lessons",
                        lambda records, ticker, as_of, same=5, other=3: future)
    problems: list[str] = []
    pit._lessons_block(problems)
    assert any("晚于分析日" in p for p in problems)


# ---- 工具事实：只用分析日（含）之前的 K 线 ----


def test_the_tool_facts_block_passes_with_a_leaky_port() -> None:
    market = CsvMarket(_MARKET_ROOT, "synthetic")
    problems: list[str] = []
    assert pit._tool_facts_block(ROOT, market, problems) == 63
    assert problems == []


def test_a_tool_fact_dated_after_the_analysis_day_is_reported(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """被篡改的工具交回 as_of 晚于分析日的事实：必须报违规。"""

    def fake_build_tools(run: object, deps: object, specs: object) -> ToolBox:
        box = ToolBox([])

        def indicator(name: str, n: int) -> dict[str, object]:
            return {"id": f"{name}_{n}", "value": 1.0, "unit": "USD",
                    "as_of": "2026-12-31"}

        box["indicator"] = indicator
        return box

    monkeypatch.setattr(pit, "build_tools", fake_build_tools)
    market = CsvMarket(_MARKET_ROOT, "synthetic")
    problems: list[str] = []
    pit._tool_facts_block(ROOT, market, problems)
    assert any("晚于分析日" in p for p in problems)
