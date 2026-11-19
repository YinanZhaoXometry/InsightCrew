"""记忆的测试：契约、两条结算路径逐项一致、hit 四种情形、select_lessons 的时点
不变量（不变量 20）、benchmark_for 的后缀约定、lesson_findings，以及两条路径
互不 import、只依赖标准库或 pydantic 的独立性断言（BLUEPRINT 19.18 WP29）。"""
from __future__ import annotations

import ast
import datetime
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from insightcrew.domain import memory_sql
from insightcrew.domain.market import Bar
from insightcrew.domain.memory import (
    DecisionRecord,
    Lesson,
    OutcomeRecord,
    benchmark_for,
    lesson_findings,
    select_lessons,
    settle,
)
from insightcrew.domain.types import Action, Rating

ROOT = Path(__file__).resolve().parent.parent

# 2026-01-05 是周一；窗口从 01-06（周二）起的工作日：06、07、08、09、12、13、14、15。
AS_OF = "2026-01-05"
DAYS = [date(2026, 1, d) for d in (6, 7, 8, 9, 12, 13, 14, 15)]
CLOSES = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 111.0, 112.0]


def decision(as_of: str = AS_OF, *, ticker: str = "ACME", action: Action = Action.BUY,
             stop: float | None = None, target: float | None = None,
             rating: Rating = Rating.BUY, run_id: str = "run-1") -> DecisionRecord:
    return DecisionRecord(kind="decision", run_id=run_id, ticker=ticker, as_of=as_of,
                          rating=rating, action=action, entry=None, stop=stop,
                          target=target, size_pct=10.0, horizon_days=5)


def outcome(dec: DecisionRecord, known_on: str, *, lesson: str | None = "教训",
            return_pct: float = 1.0, alpha_pct: float | None = 0.5,
            hit: Literal["stop", "target", "neither", "n/a"] = "neither") -> OutcomeRecord:
    return OutcomeRecord(kind="outcome", run_id=dec.run_id, ticker=dec.ticker,
                         as_of=dec.as_of, known_on=known_on, holding_days=5,
                         return_pct=return_pct, benchmark="SPY",
                         benchmark_return_pct=0.5, alpha_pct=alpha_pct, hit=hit,
                         lesson=lesson)


def bars(rows: Sequence[tuple[float, float, float]], start: int = 0) -> tuple[Bar, ...]:
    """rows 是 (close, low, high)，日期取 DAYS 里从第 start 个起的工作日。"""
    return tuple(Bar(date=DAYS[start + i], open=c, high=h, low=lo, close=c, volume=0.0)
                 for i, (c, lo, h) in enumerate(rows))


def plain_bars(closes: Sequence[float], start: int = 0) -> tuple[Bar, ...]:
    return bars([(c, c - 1, c + 1) for c in closes], start=start)


def bench_full() -> tuple[Bar, ...]:
    """基准全程覆盖：入场日前一根 400，离场日前最后一根 404，其后 410。"""
    return (Bar(date=date(2026, 1, 5), open=400, high=401, low=399, close=400, volume=0.0),
            Bar(date=date(2026, 1, 9), open=404, high=405, low=403, close=404, volume=0.0),
            Bar(date=date(2026, 1, 16), open=410, high=411, low=409, close=410, volume=0.0))


# ---- 两条路径的独立性（HANDOFF 5.5 规则 4：断言在本文件里，不动 test_architecture）


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add("." * node.level + (node.module or ""))
    return mods


def test_the_two_paths_are_independent():
    first = _imports(ROOT / "insightcrew" / "domain" / "memory.py")
    second = _imports(ROOT / "insightcrew" / "domain" / "memory_sql.py")
    assert ".memory" not in second, "路径 2 不许 import 路径 1"
    assert ".memory_sql" not in first, "路径 1 不许 import 路径 2"
    assert not any(m.startswith("insightcrew") for m in first | second), \
        "两条路径只用相对导入访问本层，不做绝对导入"
    assert not any(m.startswith(".") for m in second), "路径 2 不依赖包内任何模块"
    for name, mods in (("memory", first), ("memory_sql", second)):
        for m in mods:
            if m.startswith("."):
                continue
            top = m.split(".")[0]
            assert top == "pydantic" or top in sys.stdlib_module_names, \
                f"{name}.py 引入了 {top}：只许 pydantic 与标准库"


# ---- settle：窗口没走完就是 None


def test_settle_returns_none_until_the_window_has_traded_by_known_by():
    dec = decision()
    six = plain_bars(CLOSES[:6])
    assert settle(dec, six, bench_full(), 5, date(2026, 1, 8)) is None   # 窗口只有 3 根
    assert settle(dec, six, bench_full(), 5, date(2026, 1, 12)) is None  # 5 根，仍差离场那根
    assert memory_sql.settle(dec, six, bench_full(), 5, date(2026, 1, 12)) is None


def test_settle_ignores_bars_after_known_by():
    dec = decision()
    with_extra = plain_bars(CLOSES)                       # 多给 01-14、01-15 两根
    cut = plain_bars(CLOSES[:6])                          # known_by 之后的不算
    one = settle(dec, with_extra, bench_full(), 5, date(2026, 1, 13))
    two = settle(dec, cut, bench_full(), 5, date(2026, 1, 13))
    assert one is not None and two is not None
    assert one == two


def test_settle_rejects_a_holding_window_below_one_day():
    for fn in (settle, memory_sql.settle):
        with pytest.raises(ValueError, match="持有期"):
            fn(decision(), plain_bars(CLOSES[:6]), (), 0, date(2026, 1, 13))


# ---- settle：入场、收益、基准与超额的口径


def test_settle_enters_at_the_first_close_after_the_analysis_date():
    out = settle(decision(), plain_bars(CLOSES[:6]), (), 5, date(2026, 1, 13))
    assert out is not None
    assert out.kind == "outcome"
    assert out.run_id == "run-1" and out.ticker == "ACME" and out.as_of == AS_OF
    assert out.known_on == "2026-01-13"          # 离场那根 K 线的日期
    assert out.holding_days == 5
    assert out.return_pct == pytest.approx(10.0)  # 100 → 110
    assert out.lesson is None


def test_settle_values_the_benchmark_at_each_end_by_asof():
    out = settle(decision(), plain_bars(CLOSES[:6]), bench_full(), 5, date(2026, 1, 13))
    assert out is not None
    assert out.benchmark_return_pct == pytest.approx(1.0)   # 400 → 404（两端各取前一根收盘）
    assert out.alpha_pct == pytest.approx(9.0)
    assert out.benchmark == "SPY"


def test_settle_without_benchmark_prices_leaves_alpha_null_but_settles():
    out = settle(decision(), plain_bars(CLOSES[:6]), (), 5, date(2026, 1, 13))
    assert out is not None
    assert out.benchmark_return_pct is None and out.alpha_pct is None
    assert out.return_pct == pytest.approx(10.0)


def test_settle_with_a_late_benchmark_only_is_still_no_alpha():
    late = (Bar(date=date(2026, 1, 10), open=401, high=402, low=400, close=401, volume=0.0),)
    out = settle(decision(), plain_bars(CLOSES[:6]), late, 5, date(2026, 1, 13))
    assert out is not None
    assert out.benchmark_return_pct is None and out.alpha_pct is None


def test_settle_names_the_hk_benchmark_by_exchange_suffix():
    dec = decision(ticker="0700.HK", rating=Rating.OVERWEIGHT)
    out = settle(dec, plain_bars(CLOSES[:6]), (), 5, date(2026, 1, 13))
    assert out is not None and out.benchmark == "_HSI"


# ---- settle：hit 的四种情形


def test_hit_is_stop_when_the_stop_is_touched_first():
    rows = [(100, 99, 101), (94, 92, 96), (96, 95, 97), (97, 96, 98), (98, 97, 99), (99, 98, 100)]
    out = settle(decision(stop=95.0, target=120.0), bars(rows), (), 5, date(2026, 1, 13))
    assert out is not None and out.hit == "stop"


def test_hit_is_target_when_the_target_is_touched_first():
    rows = [(100, 99, 101), (102, 100, 104), (103, 102, 105), (104, 103, 106),
            (105, 104, 107), (106, 105, 108)]
    out = settle(decision(stop=90.0, target=103.0), bars(rows), (), 5, date(2026, 1, 13))
    assert out is not None and out.hit == "target"


def test_hit_is_neither_when_no_level_is_touched():
    out = settle(decision(stop=50.0, target=200.0), plain_bars(CLOSES[:6]),
                 (), 5, date(2026, 1, 13))
    assert out is not None and out.hit == "neither"


def test_hit_is_na_for_hold_and_the_return_is_still_computed():
    out = settle(decision(action=Action.HOLD, rating=Rating.HOLD), plain_bars(CLOSES[:6]),
                 (), 5, date(2026, 1, 13))
    assert out is not None
    assert out.hit == "n/a"
    assert out.return_pct == pytest.approx(10.0)


def test_hit_counts_a_same_bar_touch_as_a_stop():
    rows = [(100, 99, 101), (98, 92, 104), (99, 96, 103), (100, 97, 104),
            (101, 98, 105), (102, 99, 106)]
    out = settle(decision(stop=95.0, target=103.0), bars(rows), (), 5, date(2026, 1, 13))
    assert out is not None and out.hit == "stop"


# ---- 两条路径逐项一致


_CASES = [
    ("ACME", Action.BUY, 95.0, 120.0, 5, "full"),
    ("ACME", Action.BUY, None, None, 5, "none"),
    ("ACME", Action.BUY, 50.0, 200.0, 5, "spans"),
    ("0700.HK", Action.BUY, 90.0, None, 3, "late"),
    ("ACME", Action.HOLD, None, None, 5, "full"),
    ("ACME", Action.SELL, None, None, 2, "none"),
    ("0700.HK", Action.BUY, 92.0, 104.0, 4, "none"),
]


def _bench(kind: str) -> tuple[Bar, ...]:
    if kind == "full":
        return bench_full()
    if kind == "spans":
        return (Bar(date=date(2026, 1, 2), open=390, high=391, low=389, close=390, volume=0.0),
                Bar(date=date(2026, 1, 20), open=420, high=421, low=419, close=420, volume=0.0))
    if kind == "late":
        return (Bar(date=date(2026, 1, 10), open=401, high=402, low=400, close=401, volume=0.0),)
    return ()


def test_both_paths_agree_on_every_outcome():
    for ticker, action, stop, target, days, bench_kind in _CASES:
        dec = decision(ticker=ticker, action=action, stop=stop, target=target)
        rows = [(c, c - 3, c + 3) for c in CLOSES]        # 个别情形里会触到价位
        stock = bars(rows)
        bench = _bench(bench_kind)
        for known_by in (date(2026, 1, 7), date(2026, 1, 9), date(2026, 1, 12),
                         date(2026, 1, 13), date(2026, 1, 20)):
            one = settle(dec, stock, bench, days, known_by)
            two = memory_sql.settle(dec, stock, bench, days, known_by)
            if one is None or two is None:
                assert one is None and two is None, f"{ticker}/{known_by} 一条 pending 一条不是"
                continue
            assert (one.known_on, one.holding_days, one.return_pct, one.benchmark_return_pct,
                    one.alpha_pct, one.hit) == (
                two.known_on, two.holding_days, two.return_pct, two.benchmark_return_pct,
                two.alpha_pct, two.hit), f"两条路径在 {ticker}/{known_by} 不一致"


# ---- select_lessons：不变量 20 与挑选规则


def _log_records() -> list[DecisionRecord | OutcomeRecord]:
    d_acme_1 = decision(AS_OF, ticker="ACME", run_id="r1")
    d_acme_2 = decision("2026-01-06", ticker="ACME", run_id="r2")
    d_hk = decision("2026-01-07", ticker="0700.HK", run_id="r3")
    d_other = decision("2026-01-08", ticker="BRKA", run_id="r4")
    return [
        d_acme_1, outcome(d_acme_1, "2026-01-13", lesson="回调时止损要给足空间"),
        d_acme_2, outcome(d_acme_2, "2026-01-14", lesson="趋势确认后再加仓"),
        d_hk, outcome(d_hk, "2026-01-15", lesson="跨市场要看本地基准"),
        d_other, outcome(d_other, "2026-03-01", lesson="这是未来才可知的结果"),
        decision("2026-01-09", ticker="ACME", run_id="r5"),
    ]


def test_select_lessons_never_returns_a_outcome_known_after_as_of():
    picked = select_lessons(_log_records(), "ACME", date(2026, 2, 2))
    assert all(datetime.date.fromisoformat(e.known_on) <= date(2026, 2, 2) for e in picked)
    assert "这是未来才可知的结果" not in {e.lesson for e in picked}
    later = select_lessons(_log_records(), "ACME", date(2026, 3, 10))
    assert "这是未来才可知的结果" in {e.lesson for e in later}   # as_of 之后可知了才可见
    edge = select_lessons(_log_records(), "ACME", date(2026, 3, 1))
    assert "这是未来才可知的结果" in {e.lesson for e in edge}    # known_on == as_of 可见


def test_select_lessons_picks_newest_same_then_newest_other_with_caps():
    picked = select_lessons(_log_records(), "ACME", date(2026, 3, 10))
    assert [(e.ticker, e.as_of, e.lesson) for e in picked] == [
        ("ACME", "2026-01-06", "趋势确认后再加仓"),      # 本代码最新的在前
        ("ACME", "2026-01-05", "回调时止损要给足空间"),
        ("BRKA", "2026-01-08", "这是未来才可知的结果"),  # 其他代码按时间最新的在前
        ("0700.HK", "2026-01-07", "跨市场要看本地基准"),
    ]
    assert [e.known_on for e in picked] == ["2026-01-14", "2026-01-13", "2026-03-01",
                                            "2026-01-15"]
    assert picked[0].rating == "Buy" and picked[0].return_pct == 1.0
    assert picked[0].alpha_pct == 0.5 and picked[0].hit == "neither"


def test_select_lessons_honours_the_same_and_other_counts():
    picked = select_lessons(_log_records(), "ACME", date(2026, 3, 10), same=1, other=1)
    assert [e.lesson for e in picked] == ["趋势确认后再加仓", "这是未来才可知的结果"]


def test_select_lessons_skips_unwritten_lessons_and_orphan_outcomes():
    d = decision(AS_OF, ticker="ACME", run_id="r1")
    records: list[DecisionRecord | OutcomeRecord] = [
        d, outcome(d, "2026-01-13", lesson=None),          # 教训还没写
        outcome(decision("2026-01-06", ticker="ACME", run_id="r2"),
                "2026-01-14", lesson="没有决策配对的结果"),  # 决策不在记录里
    ]
    assert select_lessons(records, "ACME", date(2026, 2, 2)) == ()


# ---- benchmark_for 与 lesson_findings


def test_benchmark_for_maps_by_exchange_suffix():
    assert benchmark_for("0700.HK") == "_HSI"
    assert benchmark_for("AAPL") == "SPY"
    assert benchmark_for("SYNHK") == "SPY"          # 不以 .HK 结尾就不算港股
    assert benchmark_for("0005.HK") == "_HSI"


def test_lesson_findings_accept_a_plain_lesson():
    out = outcome(decision(), "2026-01-13")
    assert lesson_findings(out, Lesson(text="方向判断要等证据齐了再下", cites=("hit",))) == []


def test_lesson_findings_flag_empty_cites_missing_alpha_and_numbers():
    out = outcome(decision(), "2026-01-13", alpha_pct=None)
    checks = {f.check for f in lesson_findings(out, Lesson(text="见好就收", cites=()))}
    assert checks == {"lesson_cites_outcome"}
    checks = {f.check for f in lesson_findings(
        out, Lesson(text="超额给了教训", cites=("alpha_pct",)))}
    assert checks == {"lesson_cites_known_field"}
    with_alpha = outcome(decision(), "2026-01-13")
    checks = {f.check for f in lesson_findings(
        with_alpha, Lesson(text="涨了 10 点就该警惕", cites=("return_pct",)))}
    assert checks == {"lesson_text_has_no_numbers"}
    for f in lesson_findings(with_alpha, Lesson(text="涨了 10 点", cites=("alpha_pct",))):
        assert f.owner == "reflector" and f.layer == "L2"


# ---- 复盘员的提示词（尚无角色加载它，属预期；WP31 才声明）


def test_reflector_prompt_carries_attribution_reads_and_no_invented_numbers():
    text = (ROOT / "insightcrew" / "roles" / "prompts" / "reflector.md").read_text(
        encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "<!-- Adapted from TradingAgents (Apache-2.0), modified. -->"
    assert "settle/decision" in text and "settle/outcome" in text
    assert "绝不发明" in text            # 结果数字来自代码，不由模型编造
    assert "alpha_pct" in text and "n/a" in text
