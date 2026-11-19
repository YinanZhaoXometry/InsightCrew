"""情绪通道的测试：端口窗口边界（两端含）、无日期行丢弃计数、发布日之前不返回、
正文截到 280 字、坏 tag 按坏文件处理、窗口出界与缺文件是 NotCovered、坏文件是
SourceUnavailable、文件顺序保持（BLUEPRINT 19.8、19.12；排序、截断与 S1 编号是
evidence 阶段的职责），BAND_RANGES 与蓝图一致，以及 sentiment_findings 的三项检查
（19.12 的 audit additions，owner "sentiment"）。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.social_json import JsonSocial
from insightcrew.domain.market import NotCovered, SourceUnavailable
from insightcrew.domain.social import (
    BAND_RANGES,
    Band,
    Post,
    PostEntry,
    SentimentAnalysis,
    SentimentSignal,
    SocialSet,
    sentiment_findings,
)

ROOT = Path(__file__).resolve().parent.parent
PORT = JsonSocial(ROOT / "data" / "market", "synthetic")
GENERATOR = ROOT / "data" / "make_social.py"
AS_OF = date(2026, 9, 30)


def window(as_of: date, days: int = 7):
    return as_of - timedelta(days=days), as_of


def urls(posts) -> list[str]:
    return [post.url.rsplit("/", 1)[-1] for post in posts]


# ---- 生成器 ----


def test_generator_is_deterministic():
    first = subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    files = {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/social").iterdir())}
    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    assert {p.name: p.read_bytes() for p in sorted((ROOT / "data/market/synthetic/social").iterdir())} == files
    assert set(files) == {"ACME.json"}
    assert first.returncode == 0


# ---- 窗口与状态 ----


def test_acme_on_2026_09_30_with_a_7_day_window_holds_four_posts_and_one_dropped():
    ss = PORT.posts("ACME", *window(AS_OF))                         # [2026-09-23, 2026-09-30]
    assert urls(ss.posts) == ["s0002", "s0003", "s0005", "s0006"]   # 09-23、09-25、09-28、09-30
    assert ss.dropped == 1                                          # 无日期的那一条
    assert ss.source == "合成数据"
    assert all(isinstance(post.published, date) for post in ss.posts)


def test_both_window_ends_are_inclusive():
    start = date(2026, 9, 23)                                       # 恰好是 s0002 的发布日
    ss = PORT.posts("ACME", start, date(2026, 9, 30))               # 恰好是 s0006 的发布日
    assert "s0002" in urls(ss.posts) and "s0006" in urls(ss.posts)
    one_day = PORT.posts("ACME", date(2026, 9, 23), date(2026, 9, 23))
    assert urls(one_day.posts) == ["s0002"]                         # 单日窗口只含当天的帖子


def test_a_post_is_not_returned_before_its_date():
    ss = PORT.posts("ACME", *window(AS_OF))
    assert "s0007" not in urls(ss.posts)                            # 10-02 发布，09-30 看不到
    later = PORT.posts("ACME", *window(date(2026, 10, 2)))
    assert "s0007" in urls(later.posts)


def test_posts_keep_file_order_within_the_window():
    ss = PORT.posts("ACME", date(2025, 1, 1), date(2026, 12, 31))
    assert urls(ss.posts) == [f"s{n:04d}" for n in (1, 2, 3, 5, 6, 7)]      # s0004 无日期，丢弃
    assert ss.dropped == 1


def test_the_shape_of_the_returned_set():
    ss = PORT.posts("ACME", *window(AS_OF))
    assert isinstance(ss, SocialSet)
    first = ss.posts[0]
    assert isinstance(first, Post)
    assert first.source == "合成数据" and first.tag == "none"
    assert first.text == "看了采购协议的新闻，成本端这一下应该稳了"


def test_text_is_truncated_to_280_characters(tmp_path: Path):
    long = "涨" * 300
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "posts": [{"published": "2026-09-27", "source": "测试", "text": long,
                               "url": "https://example.com/t", "tag": "bullish"}]},
                   ensure_ascii=False), encoding="utf-8")
    ss = JsonSocial(tmp_path, "synthetic").posts("ACME", *window(AS_OF))
    assert len(ss.posts[0].text) == 280 and ss.posts[0].text == "涨" * 280
    assert ss.dropped == 0                                          # 截断不算丢弃


# ---- 错误分类 ----


def test_a_date_after_the_cover_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.posts("ACME", *window(date(2027, 6, 30)))              # as_of 晚于 covers_to


def test_a_window_starting_before_the_cover_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.posts("ACME", date(2024, 12, 31), AS_OF)               # start 早于 covers_from


def test_a_ticker_without_a_file_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.posts("THIN", *window(AS_OF))


def test_covers_are_read_from_the_file(tmp_path: Path):
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2026-06-01", "covers_to": "2026-12-31",
                    "posts": [{"published": "2026-08-06", "source": "测试", "text": "一条帖子",
                               "url": "https://example.com/x", "tag": "none"}]},
                   ensure_ascii=False), encoding="utf-8")
    port = JsonSocial(tmp_path, "synthetic")
    ok = port.posts("ACME", date(2026, 6, 1), date(2026, 9, 30))
    assert urls(ok.posts) == ["x"]
    with pytest.raises(NotCovered):                                 # start 早于 covers_from
        port.posts("ACME", date(2026, 5, 31), date(2026, 9, 30))


def test_broken_json_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text("{ 不是 JSON", encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonSocial(tmp_path, "synthetic").posts("ACME", *window(AS_OF))


def test_unknown_field_is_refused(tmp_path: Path):
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    post = {"published": "2026-08-06", "source": "测试", "text": "t", "url": "u",
            "tag": "none", "extra": 1}
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "posts": [post]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonSocial(tmp_path, "synthetic").posts("ACME", *window(AS_OF))


def test_a_missing_field_is_source_unavailable(tmp_path: Path):
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    post = {"published": "2026-08-06", "source": "测试", "text": "t"}       # 缺 url 与 tag
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "posts": [post]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonSocial(tmp_path, "synthetic").posts("ACME", *window(AS_OF))


def test_an_unknown_tag_is_a_broken_file(tmp_path: Path):
    (tmp_path / "synthetic" / "social").mkdir(parents=True)
    post = {"published": "2026-08-06", "source": "测试", "text": "t",
            "url": "u", "tag": "excited"}
    (tmp_path / "synthetic" / "social" / "ACME.json").write_text(
        json.dumps({"source": "测试", "covers_from": "2025-01-01", "covers_to": "2026-12-31",
                    "posts": [post]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonSocial(tmp_path, "synthetic").posts("ACME", *window(AS_OF))


# ---- 六档情感带 ----


def test_band_ranges_match_the_blueprint():
    assert BAND_RANGES == {"bearish": (0, 2), "mildly_bearish": (2, 4), "neutral": (4, 6),
                           "mixed": (4, 6), "mildly_bullish": (6, 8), "bullish": (8, 10)}


# ---- sentiment_findings（19.12 的三项检查，全部手工构造输入）----


def _post(n: int) -> PostEntry:
    return PostEntry(id=f"S{n}", published="2026-09-27", source="合成数据",
                     text=f"第 {n} 条帖子", tag="none")


def _analysis(band: Band = "mixed", score: float = 5.0, signals=()) -> SentimentAnalysis:
    return SentimentAnalysis(band=band, score=score, confidence="medium",
                             signals=tuple(signals), narrative="叙述")


def test_no_signal_when_posts_exist_is_a_finding():
    out = sentiment_findings([_post(1)], _analysis(signals=()))
    assert [f.check for f in out] == ["sentiment_has_signals"]
    assert out[0].owner == "sentiment" and out[0].layer == "L2"


def test_no_finding_when_there_are_no_posts():
    assert sentiment_findings([], _analysis(signals=())) == []


def test_a_signal_must_cite_a_known_post():
    ok = sentiment_findings([_post(1)],
                            _analysis(signals=[SentimentSignal(post="S1", direction="positive",
                                                               reading="依据")]))
    assert ok == []
    bad = sentiment_findings([_post(1)],
                             _analysis(signals=[SentimentSignal(post="S9", direction="positive",
                                                                reading="依据")]))
    assert [f.check for f in bad] == ["sentiment_signal_cites_known_post"]
    assert bad[0].subject == "sentiment.signals[0]"
    assert "S9" in bad[0].message


def test_a_score_outside_its_band_is_a_finding():
    out = sentiment_findings([_post(1)],
                             _analysis(band="bullish", score=5.0,
                                       signals=[SentimentSignal(post="S1", direction="positive",
                                                                reading="依据")]))
    assert [f.check for f in out] == ["sentiment_score_matches_band"]
    assert out[0].actual == 5.0 and out[0].limit == 8            # bullish 是 (8, 10)，越过的下界是 8


def test_band_edges_are_inclusive():
    cases: tuple[tuple[Band, float], ...] = (
        ("bearish", 0.0), ("bearish", 2.0), ("neutral", 4.0), ("mixed", 6.0),
        ("mildly_bullish", 6.0), ("bullish", 8.0), ("bullish", 10.0))
    for band, score in cases:
        out = sentiment_findings([], _analysis(band=band, score=score))
        assert out == [], f"{band} 的 {score} 分不应有发现"


def test_every_finding_is_owned_by_sentiment():
    out = sentiment_findings([_post(1), _post(2)],
                             _analysis(band="bearish", score=9.5,
                                       signals=[SentimentSignal(post="S9", direction="positive",
                                                                reading="依据")]))
    assert {f.owner for f in out} == {"sentiment"}
    assert [f.check for f in out] == ["sentiment_signal_cites_known_post", "sentiment_score_matches_band"]
