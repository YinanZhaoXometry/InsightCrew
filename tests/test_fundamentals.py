"""基本面的测试：注册表与两条路径同步、两条路径逐点一致、重述按时点生效、
端口错误分类、每一个算不出的情形（BLUEPRINT 18.13 第二行）。"""
from __future__ import annotations

import datetime
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.declarations import load_fundamentals
from insightcrew.adapters.statements_json import JsonStatements
from insightcrew.domain import fundamentals, fundamentals_sql
from insightcrew.domain.fundamentals_ledger import build_fundamentals, recompute_fundamentals
from insightcrew.domain.ledger import disagreements
from insightcrew.domain.market import Instrument, NotCovered, SourceUnavailable
from insightcrew.domain.statements import Reported, StatementSet

ROOT = Path(__file__).resolve().parent.parent
SPECS = load_fundamentals(ROOT / "data" / "fundamentals.toml")
PORT = JsonStatements(ROOT / "data" / "market", "synthetic")
ACME = Instrument(ticker="ACME", name="Acme Corp", market="US", currency="USD",
                  benchmark="^SPX", lot=0, synthetic=True)
HK = ACME.model_copy(update={"ticker": "0700.HK", "market": "HK", "currency": "HKD"})

_START, _END = date(2024, 3, 1), date(2026, 12, 31)
SWEEP = sorted({_START + timedelta(days=n)
                for n in range(0, (_END - _START).days + 1, 47)}
               | {date(2026, 2, 10), date(2026, 2, 19), date(2026, 5, 29),
                  date(2026, 8, 14), date(2026, 9, 30), date(2026, 11, 30)})


def build(as_of: date, close: float = 100.0, instrument: Instrument = ACME):
    ss = PORT.statements("ACME", as_of)
    return build_fundamentals(instrument, ss, close, as_of, as_of, SPECS), ss


def by_id(facts):
    return {f.id: f for f in facts}


# ---- 注册表与两条路径 ----


def test_registry_has_twelve_facts_and_both_paths_know_every_function():
    assert len(SPECS) == 12
    assert {s.key for s in SPECS} == {
        "revenue_fy", "net_income_fy", "eps_diluted_fy", "operating_cash_flow_fy",
        "total_assets_fy", "total_liabilities_fy", "revenue_yoy_pct", "net_income_yoy_pct",
        "net_margin_pct", "liabilities_to_assets_pct", "pe_fy", "filing_age_days"}
    for spec in SPECS:
        assert callable(getattr(fundamentals, spec.fn, None)), f"路径 1 缺 {spec.fn}"
        assert callable(getattr(fundamentals_sql, spec.fn, None)), f"路径 2 缺 {spec.fn}"


def test_money_facts_are_in_millions():
    (facts, _card), _ = build(date(2026, 9, 30))
    revenue = by_id(facts)["revenue_fy"]
    assert revenue.value == 1081.0                       # 1,081,000,000 → 1081 百万
    assert revenue.unit == "USD million"
    assert revenue.bars_used == 0
    assert revenue.source == "fundamentals.latest"
    assert revenue.label.endswith("（最近财年，截至 2025-12-31）")


# ---- 重述与时点（18.7 的 ACME 表）----


def test_restatement_served_by_analysis_date():
    (at_0210, _), _ = build(date(2026, 2, 10))
    (at_0529, _), _ = build(date(2026, 5, 29))
    (at_0930, card), _ = build(date(2026, 9, 30))
    assert by_id(at_0210)["revenue_fy"].value == 940.0   # 2025 财年尚未申报，读 2024 财年
    assert by_id(at_0529)["revenue_fy"].value == 1105.0  # 10-K 版
    assert by_id(at_0930)["revenue_fy"].value == 1081.0  # 8-14 的 10-K/A 重述版
    assert len(card.warnings) == 3            # 10-K/A 重述了 revenue、net_income、eps_diluted
    revenue_warning = next(w for w in card.warnings if w.startswith("revenue"))
    assert "2026-02-19" in revenue_warning and "2026-08-14" in revenue_warning


def test_row_filed_after_the_last_bar_never_reaches_an_earlier_date():
    for as_of in (date(2026, 9, 30), date(2026, 10, 31)):
        (facts, _), _ = build(as_of)
        assert by_id(facts)["revenue_fy"].value == 1081.0
    (at_1130, _), _ = build(date(2026, 11, 30))           # 端口自己的测试读到 11-05 的再次重述
    assert by_id(at_1130)["revenue_fy"].value == 1090.0


def test_dates_after_the_cover_are_not_covered():
    with pytest.raises(NotCovered):
        PORT.statements("ACME", date(2027, 1, 4))


def test_missing_file_is_not_covered():
    with pytest.raises(NotCovered):
        PORT.statements("0000.HK", date(2026, 9, 30))


# ---- 两条路径逐点一致 ----


def test_paths_agree_at_every_swept_date():
    for as_of in SWEEP:
        (facts, _card), ss = build(as_of)
        computed = recompute_fundamentals(ss, 100.0, as_of, SPECS)
        assert disagreements(facts, computed) == [], f"两条路径在 {as_of} 不一致"


def test_every_none_case_agrees_between_the_paths():
    one_year = StatementSet(source="测试", currency="USD", rows=(
        Reported(concept="revenue", period_end=date(2025, 12, 31), period_days=365,
                 value=100.0, filed=date(2026, 2, 1), form="10-K"),))
    zero_prev = StatementSet(source="测试", currency="USD", rows=(
        Reported(concept="revenue", period_end=date(2024, 12, 31), period_days=365,
                 value=0.0, filed=date(2025, 2, 1), form="10-K"),
        Reported(concept="revenue", period_end=date(2025, 12, 31), period_days=365,
                 value=100.0, filed=date(2026, 2, 1), form="10-K")))
    no_denominator = StatementSet(source="测试", currency="USD", rows=(
        Reported(concept="net_income", period_end=date(2025, 12, 31), period_days=365,
                 value=50.0, filed=date(2026, 2, 1), form="10-K"),))
    zero_denominator = StatementSet(source="测试", currency="USD", rows=(
        Reported(concept="net_income", period_end=date(2025, 12, 31), period_days=365,
                 value=50.0, filed=date(2026, 2, 1), form="10-K"),
        Reported(concept="revenue", period_end=date(2025, 12, 31), period_days=365,
                 value=0.0, filed=date(2026, 2, 1), form="10-K")))
    bad_eps = StatementSet(source="测试", currency="USD", rows=(
        Reported(concept="eps_diluted", period_end=date(2025, 12, 31), period_days=365,
                 value=0.0, filed=date(2026, 2, 1), form="10-K"),))

    as_of = date(2026, 6, 1)
    cases = {
        "latest": (no_denominator, ("revenue",)),         # 文件里没有这个概念
        "yoy_pct": (one_year, ("revenue",)),              # 没有上一财年
        "yoy_pct_zero": (zero_prev, ("revenue",)),        # 上一财年为零
        "ratio_pct": (no_denominator, ("net_income", "revenue")),
        "ratio_pct_zero": (zero_denominator, ("net_income", "revenue")),
        "price_multiple": (bad_eps, ("eps_diluted",)),    # 每股收益为零
        "filing_age": (no_denominator, ("revenue",)),
    }
    for name, (ss, concepts) in cases.items():
        fn = "yoy_pct" if name.startswith("yoy") else "price_multiple" if name.startswith("price") \
            else "filing_age" if name.startswith("filing") else "ratio_pct" if name.startswith("ratio") \
            else "latest"
        one = getattr(fundamentals, fn)(ss.rows, concepts, close=100.0, as_of=as_of)
        two = getattr(fundamentals_sql, fn)(fundamentals_sql.load(ss.rows), concepts,
                                            close=100.0, as_of=as_of)
        assert one is None and two is None, f"{name}：两条路径应当都算不出"


def test_negative_figure_gives_no_multiple():
    rows = (Reported(concept="eps_diluted", period_end=date(2025, 12, 31), period_days=365,
                     value=-1.5, filed=date(2026, 2, 1), form="10-K"),)
    as_of = date(2026, 6, 1)
    assert fundamentals.price_multiple(rows, ("eps_diluted",), close=100.0, as_of=as_of) is None
    assert fundamentals_sql.price_multiple(fundamentals_sql.load(rows), ("eps_diluted",),
                                           close=100.0, as_of=as_of) is None


# ---- 账本：货币、as_of、数据卡 ----


def test_pe_fy_is_unavailable_when_the_currency_differs():
    (facts, card), ss = build(date(2026, 9, 30), instrument=HK)
    assert "pe_fy" not in by_id(facts)
    unavailable = {u.id for u in card.unavailable}
    assert "pe_fy" in unavailable
    reason = next(u.reason for u in card.unavailable if u.id == "pe_fy")
    assert "HKD" in reason and "USD" in reason
    others = tuple(s for s in SPECS if s.fn != "price_multiple")
    assert disagreements(facts, recompute_fundamentals(ss, 100.0, date(2026, 9, 30), others)) == []


def test_pe_fy_as_of_takes_the_later_of_filing_and_last_bar():
    ss = PORT.statements("ACME", date(2026, 9, 30))
    late_bar = build_fundamentals(ACME, ss, 100.0, date(2026, 9, 29), date(2026, 9, 30), SPECS)
    early_bar = build_fundamentals(ACME, ss, 100.0, date(2026, 1, 5), date(2026, 9, 30), SPECS)
    pe_late = by_id(late_bar[0])["pe_fy"]
    pe_early = by_id(early_bar[0])["pe_fy"]
    assert pe_late.as_of == "2026-09-29"                 # bar 晚于申报
    assert pe_early.as_of == "2026-08-14"                # 重述申报晚于 bar
    assert pe_late.value == pytest.approx(100.0 / 1.78)  # 10-K/A 的每股收益


def test_card_profiles_the_file():
    (facts, card), _ = build(date(2026, 9, 30))
    assert card.source == "合成数据" and card.currency == "USD"
    assert card.fiscal_year_end == "2025-12-31"
    assert card.filed == "2026-08-14"                    # 2026-11-05 的行不在分析日之内
    assert card.rows == 21                                # 22 行减去 11-05 那一行
    assert all(f.as_of <= "2026-09-30" for f in facts)    # 18.4：as_of 绝不晚于分析日


# ---- 端口的文件错误分类（18.7）----


def _port_with(tmp_path: Path, text: str) -> JsonStatements:
    (tmp_path / "synthetic" / "statements").mkdir(parents=True)
    (tmp_path / "synthetic" / "statements" / "ACME.json").write_text(text, encoding="utf-8")
    return JsonStatements(tmp_path, "synthetic")


_GOOD_ROW = ('{"concept": "revenue", "period_end": "2025-12-31", "period_days": 365,'
             ' "value": 1105.0, "filed": "2026-02-19", "form": "10-K"}')
_GOOD_HEAD = '{"source": "测试", "currency": "USD", "covers_to": "2026-12-31", "rows": ['


def test_duplicate_rows_make_the_file_malformed(tmp_path: Path):
    port = _port_with(tmp_path, _GOOD_HEAD + _GOOD_ROW + ", " + _GOOD_ROW + "]}")
    with pytest.raises(SourceUnavailable):
        port.statements("ACME", date(2026, 9, 30))


def test_missing_field_makes_the_file_malformed(tmp_path: Path):
    bad_row = '{"concept": "revenue", "period_end": "2025-12-31", "value": 1.0}'
    port = _port_with(tmp_path, _GOOD_HEAD + bad_row + "]}")
    with pytest.raises(SourceUnavailable):
        port.statements("ACME", date(2026, 9, 30))


def test_broken_json_is_source_unavailable(tmp_path: Path):
    port = _port_with(tmp_path, "{ 不是 JSON")
    with pytest.raises(SourceUnavailable):
        port.statements("ACME", date(2026, 9, 30))


def test_unknown_field_is_refused(tmp_path: Path):
    row = json.loads(_GOOD_ROW)
    row["extra"] = 1
    port = _port_with(tmp_path, _GOOD_HEAD + json.dumps(row) + "]}")
    with pytest.raises(SourceUnavailable):
        port.statements("ACME", date(2026, 9, 30))


def test_the_port_never_returns_a_row_filed_after_the_date():
    ss = PORT.statements("ACME", date(2026, 9, 30))
    assert all(r.filed <= date(2026, 9, 30) for r in ss.rows)
    assert isinstance(ss.rows[0].period_end, datetime.date)
