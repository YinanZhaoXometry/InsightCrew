"""新闻通道的测试：端口窗口边界（两端含）、无日期行丢弃计数、发布日之前不返回、
窗口出界与缺文件是 NotCovered、坏文件是 SourceUnavailable、文件顺序保持
（BLUEPRINT 19.8、19.12；排序、截断与 N1 编号是 evidence 阶段的职责），
以及 news_findings 的三项检查（19.12 的 audit additions，owner "news"）。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.news_json import JsonNews
from insightcrew.domain.macro import MarketEntry
from insightcrew.domain.market import NotCovered, SourceUnavailable
from insightcrew.domain.news import (
    Headline,
    NewsAnalysis,
    NewsEntry,
    NewsSet,
    NewsSignal,
    news_findings,
)
from insightcrew.domain.types import Claim, Fact

ROOT = Path(__file__).resolve().parent.parent
PORT = JsonNews(ROOT / "data" / "market", "synthetic")
GENERATOR = ROOT / "data" / "make_news.py"
AS_OF = date(2026, 9, 30)


def window(as_of: date, days: int = 7):
    return as_of - timedelta(days=days), as_of


def urls(items) -> list[str]:
    return [item.url.rsplit("/", 1)[-1] for item in items]


# ---- 生成器 ----


def test_generator_is_deterministic():
    first = subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    files = {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/news").iterdir())}
    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    assert {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/news").iterdir())} == files
    assert set(files) == {"ACME.json"}
    assert first.returncode == 0


# ---- 窗口与状态 ----


def test_acme_on_2026_09_30_with_a_7_day_window_holds_four_items_and_one_dropped():
    ns = PORT.headlines("ACME", *window(AS_OF))                     # [2026-09-23, 2026-09-30]
    assert urls(ns.items) == ["n0003", "n0004", "n0006", "n0007"]   # 09-23、09-24、09-27、09-30
    assert ns.dropped == 1                                          # 无日期的那一条
    assert ns.source == "合成数据"
    assert all(isinstance(item.published, date) for item in ns.items)


def test_both_window_ends_are_inclusive():
    start = date(2026, 9, 23)                                       # 恰好是 n0003 的发布日
    ns = PORT.headlines("ACME", start, date(2026, 9, 30))           # 恰好是 n0007 的发布日
    assert "n0003" in urls(ns.items) and "n0007" in urls(ns.items)
    one_day = PORT.headlines("ACME", date(2026, 9, 23), date(2026, 9, 23))
    assert urls(one_day.items) == ["n0003"]                         # 单日窗口只含当天的头条


def test_an_item_is_not_returned_before_its_date():
    ns = PORT.headlines("ACME", *window(AS_OF))
    assert "n0008" not in urls(ns.items)                            # 10-03 发布，09-30 看不到
    later = PORT.headlines("ACME", *window(date(2026, 10, 3)))
    assert "n0008" in urls(later.items)


def test_items_keep_file_order_within_the_window():
    ns = PORT.headlines("ACME", date(2025, 1, 1), date(2026, 12, 31))
    assert urls(ns.items) == [f"n{n:04d}" for n in (1, 2, 3, 4, 6, 7, 8)]   # n0005 无日期，丢弃
    assert ns.dropped == 1


# ---- 错误分类 ----


def test_a_date_after_the_cover_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.headlines("ACME", *window(date(2027, 6, 30)))          # as_of 晚于 covers_to


def test_a_window_starting_before_the_cover_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.headlines("ACME", date(2024, 12, 31), AS_OF)           # start 早于 covers_from


def test_a_ticker_without_a_file_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.headlines("THIN", *window(AS_OF))


def test_covers_are_read_from_the_file(tmp_path: Path):
    (tmp_path / "synthetic" / "news").mkdir(parents=True)
    (tmp_path / "synthetic" / "news" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2026-06-01", "covers_to": "2026-12-31",
                    "items": [{"published": "2026-08-06", "source": "测试",
                               "title": "一条新闻", "url": "https://example.com/x"}]},
                   ensure_ascii=False), encoding="utf-8")
    port = JsonNews(tmp_path, "synthetic")
    ok = port.headlines("ACME", date(2026, 6, 1), date(2026, 9, 30))
    assert urls(ok.items) == ["x"]
    with pytest.raises(NotCovered):                                 # start 早于 covers_from
        port.headlines("ACME", date(2026, 5, 31), date(2026, 9, 30))


def test_broken_json_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "news").mkdir(parents=True)
    (tmp_path / "synthetic" / "news" / "ACME.json").write_text("{ 不是 JSON", encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonNews(tmp_path, "synthetic").headlines("ACME", *window(AS_OF))


def test_unknown_field_is_refused(tmp_path: Path):
    (tmp_path / "synthetic" / "news").mkdir(parents=True)
    item = {"published": "2026-08-06", "source": "测试", "title": "t", "url": "u", "extra": 1}
    (tmp_path / "synthetic" / "news" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "items": [item]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonNews(tmp_path, "synthetic").headlines("ACME", *window(AS_OF))


def test_a_missing_field_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "news").mkdir(parents=True)
    item = {"published": "2026-08-06", "source": "测试", "title": "t"}     # 缺 url
    (tmp_path / "synthetic" / "news" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "items": [item]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonNews(tmp_path, "synthetic").headlines("ACME", *window(AS_OF))


def test_the_shape_of_the_returned_set():
    ns = PORT.headlines("ACME", *window(AS_OF))
    assert isinstance(ns, NewsSet)
    first = ns.items[0]
    assert isinstance(first, Headline)
    assert first.source == "合成数据" and first.title == "ACME 管理层在机构调研中介绍第四季度排产计划"
    assert first.url == "https://example.com/news/n0003"


# ---- news_findings（19.12 的三项检查，全部手工构造输入）----


def _entry(n: int) -> NewsEntry:
    return NewsEntry(id=f"N{n}", published="2026-09-27", source="合成数据",
                     title=f"第 {n} 条新闻", url=f"https://example.com/news/x{n}")


def _market(p: int) -> MarketEntry:
    return MarketEntry(id=f"P{p}", question="第四季度会加息吗？", probability=0.3,
                       volume=12000.0, end_date="2026-12-31", fetched_on="2026-09-30")


def _fact(fid: str, value: float) -> Fact:
    return Fact(id=fid, label=fid, value=value, unit="", as_of="2026-09-30",
                source="macro.build_macro", bars_used=0)


def _analysis(signals=(), claims=()) -> NewsAnalysis:
    return NewsAnalysis(signals=tuple(signals), claims=tuple(claims), stance="neutral",
                        confidence="medium", narrative="叙述")


def test_no_signal_when_items_exist_is_a_finding():
    out = news_findings([_entry(1)], [], [], _analysis())
    assert [f.check for f in out] == ["news_has_signals"]
    assert out[0].owner == "news" and out[0].layer == "L2"


def test_markets_alone_also_require_a_signal():
    out = news_findings([], [_market(1)], [], _analysis())
    assert [f.check for f in out] == ["news_has_signals"]


def test_no_finding_when_there_is_nothing_to_read():
    assert news_findings([], [], [], _analysis()) == []


def test_a_signal_must_cite_a_known_item():
    ok = news_findings([_entry(1)], [], [],
                       _analysis(signals=[NewsSignal(item="N1", direction="positive", reading="依据")]))
    assert ok == []
    bad = news_findings([_entry(1)], [], [],
                        _analysis(signals=[NewsSignal(item="N9", direction="positive", reading="依据")]))
    assert [f.check for f in bad] == ["news_signal_cites_known_item"]
    assert bad[0].subject == "news.signals[0]"
    assert "N9" in bad[0].message


def test_a_signal_may_cite_a_market_entry():
    out = news_findings([], [_market(1)], [],
                        _analysis(signals=[NewsSignal(item="P1", direction="neutral", reading="依据")]))
    assert out == []


def test_claims_are_checked_against_macro_facts():
    facts = [_fact("macro_cpi", 2.9)]
    signal = [NewsSignal(item="N1", direction="positive", reading="依据")]
    ok = news_findings([_entry(1)], [], facts,
                       _analysis(signals=signal,
                                 claims=[Claim(fact="macro_cpi", value=2.9, reading="通胀温和")]))
    assert ok == []
    unknown = news_findings([_entry(1)], [], facts,
                            _analysis(signals=signal,
                                      claims=[Claim(fact="macro_gdp", value=1.0, reading="无关")]))
    assert [f.check for f in unknown] == ["claim_cites_known_fact"]
    misquoted = news_findings([_entry(1)], [], facts,
                              _analysis(signals=signal,
                                        claims=[Claim(fact="macro_cpi", value=9.9, reading="引用错了")]))
    assert [f.check for f in misquoted] == ["claim_value_matches_fact"]
    assert misquoted[0].limit == 2.9 and misquoted[0].actual == 9.9


def test_empty_claims_are_allowed():
    out = news_findings([_entry(1)], [], [],
                        _analysis(signals=[NewsSignal(item="N1", direction="neutral", reading="依据")]))
    assert out == []                                               # 宏观通道关闭时 claims 允许为空


def test_every_finding_is_owned_by_news():
    out = news_findings([_entry(1)], [_market(1)], [_fact("macro_cpi", 2.9)],
                        _analysis(signals=[NewsSignal(item="X9", direction="neutral", reading="依据"),
                                           NewsSignal(item="P1", direction="neutral", reading="依据")],
                                  claims=[Claim(fact="macro_gdp", value=1.0, reading="不存在")]))
    assert {f.owner for f in out} == {"news"}
    assert [f.check for f in out] == ["news_signal_cites_known_item", "claim_cites_known_fact"]
