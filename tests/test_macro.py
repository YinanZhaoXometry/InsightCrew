"""宏观核心的测试：两条路径独立、逐月末一致、版本时点（重述与晚出版本）、
build_macro 的事实与算不出、声明装载器的错误分类、档案端口的窗口与错误分类、
合成数据的要求（覆盖、后来重述、晚于 2026-09-30 的版本）与生成器确定性
（BLUEPRINT 19.7、19.8、19.12、19.18 WP28）。

两条路径的独立性在这里断言，不进 tests/test_architecture.py（WP22 正同时在改它）。
"""
from __future__ import annotations

import ast
import datetime
import json
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from insightcrew.adapters.declarations_macro import load_macro
from insightcrew.adapters.macro_json import JsonMacro
from insightcrew.domain import macro, macro_sql
from insightcrew.domain.macro import MacroSpec, Observation, Series, build_macro
from insightcrew.domain.market import NotCovered, SourceUnavailable

ROOT = Path(__file__).resolve().parent.parent
SPECS = load_macro(ROOT / "data" / "macro.toml")
PORT = JsonMacro(ROOT / "data" / "market", "synthetic")
GENERATOR = ROOT / "data" / "make_macro.py"
ALIASES = ("fed_funds", "cpi", "unemployment", "us10y")

# 月末扫过 2025-01 到 2026-12；分析日再单加最后 K 线日（2026-09-30）与一个更晚的日子。
MONTH_ENDS = [macro.end_of_month(datetime.date(y, m, 1))
              for y in (2025, 2026) for m in range(1, 13)]
AS_OFS = (date(2026, 9, 30), date(2026, 12, 31))


def raw(alias: str) -> tuple[Observation, ...]:
    """从合成档案读未经端口过滤的原始观测（含将来才发布的版本），喂给两条路径。"""
    path = ROOT / "data" / "market" / "synthetic" / "macro" / f"{alias}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Observation(date=datetime.date.fromisoformat(o["date"]),
                             value=o["value"],
                             realtime_start=datetime.date.fromisoformat(o["realtime_start"]))
                 for o in data["observations"])


def obs(period: str, value: float, rt: str) -> Observation:
    return Observation(date=datetime.date.fromisoformat(period), value=value,
                       realtime_start=datetime.date.fromisoformat(rt))


def transform_of(ident: str) -> str:
    """从事实 ID 看出变换名：macro_<alias>[_chg_3m|_yoy]。"""
    assert ident.startswith("macro_")
    for suffix in ("_chg_3m", "_yoy"):
        if ident.endswith(suffix):
            return suffix[1:]
    return "level"


# ---- 两条路径的独立（在.test_macro 里断言，不进.test_architecture） ----


def _imports(path: Path) -> list[tuple[str, str]]:
    """产出 (顶层包名, 模块名或空)。只看 import 语句本身，不执行任何代码。"""
    seen: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            seen += [(a.name.split(".")[0], a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = "insightcrew" if node.level else module.split(".")[0]
            seen.append((top, module))
    return seen


def test_the_two_paths_are_independent():
    """macro.py 与 macro_sql.py 互不 import，且各自只引入标准库或 pydantic（19.7）。"""
    domain = ROOT / "insightcrew" / "domain"
    for name, other in (("macro", "macro_sql"), ("macro_sql", "macro")):
        found = _imports(domain / f"{name}.py")
        assert not any(mod == other or mod.endswith(f".{other}") for _, mod in found), \
            f"{name}.py 不允许引入 {other}"
        tops = {top for top, _ in found}
        allowed = set(sys.stdlib_module_names) | {"pydantic", "pydantic_core", "insightcrew"}
        assert tops <= allowed, f"{name}.py 只用标准库或 pydantic，多了 {tops - allowed}"


# ---- 声明与装载器 ----


def test_registry_declares_the_four_series_and_both_paths_know_every_transform():
    assert [s.alias for s in SPECS] == list(ALIASES)
    assert {s.alias: s.fred_id for s in SPECS} == {"fed_funds": "FEDFUNDS", "cpi": "CPIAUCSL",
                                                   "unemployment": "UNRATE", "us10y": "DGS10"}
    assert {s.alias: s.unit for s in SPECS} == {"fed_funds": "pct", "cpi": "index",
                                                "unemployment": "pct", "us10y": "pct"}
    for spec in SPECS:
        assert spec.transforms == ("level", "chg_3m", "yoy")
        for transform in spec.transforms:
            assert callable(getattr(macro, transform)), f"路径 1 缺 {transform}"
            assert callable(getattr(macro_sql, transform)), f"路径 2 缺 {transform}"


def _toml(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_loader_rejects_unknown_and_misplaced_keys(tmp_path: Path):
    with pytest.raises(ValueError, match="不该在表里出现"):
        load_macro(_toml(tmp_path, "a.toml", '[cpi]\nalias = "cpi"\nfred_id = "CPIAUCSL"\n'
                         'label = "x"\nunit = "index"\ntransforms = ["level"]\n'))
    with pytest.raises(ValueError, match="无法校验"):
        load_macro(_toml(tmp_path, "b.toml", '[cpi]\nfred_id = "CPIAUCSL"\nlabel = "x"\n'
                         'unit = "index"\ntransforms = ["level"]\nextra = 1\n'))
    with pytest.raises(ValueError, match="应该是数组"):
        load_macro(_toml(tmp_path, "c.toml", '[cpi]\nfred_id = "CPIAUCSL"\nlabel = "x"\n'
                         'unit = "index"\ntransforms = "level"\n'))
    with pytest.raises(ValueError, match="无法校验"):
        load_macro(_toml(tmp_path, "d.toml", '[cpi]\nfred_id = "CPIAUCSL"\nlabel = "x"\n'
                         'unit = "index"\ntransforms = ["level", "yoy_pct"]\n'))


def test_loader_turns_toml_lists_into_tuples(tmp_path: Path):
    specs = load_macro(_toml(tmp_path, "one.toml",
                             '[cpi]\nfred_id = "CPIAUCSL"\nlabel = "消费者价格指数"\n'
                             'unit = "index"\ntransforms = ["level", "yoy"]\n'))
    assert specs == (MacroSpec(alias="cpi", fred_id="CPIAUCSL", label="消费者价格指数",
                               unit="index", transforms=("level", "yoy")),)


# ---- 两条路径逐月末一致 ----


def test_both_paths_agree_on_every_synthetic_series_at_every_month_end():
    for alias in ALIASES:
        observations = raw(alias)
        with closing(macro_sql.load(observations)) as db:
            for month_end in MONTH_ENDS:
                for as_of in {month_end, *AS_OFS}:
                    for transform in ("level", "chg_3m", "yoy"):
                        first = getattr(macro, transform)(observations,
                                                          month_end=month_end, as_of=as_of)
                        second = getattr(macro_sql, transform)(db,
                                                               month_end=month_end, as_of=as_of)
                        assert first == second, \
                            f"{alias} 的 {transform} 在 {month_end}（as_of {as_of}）不一致"


def test_paths_agree_with_build_macro_at_the_last_bar_date():
    as_of = date(2026, 9, 30)
    series = [Series(alias=a, source="合成数据", observations=raw(a)) for a in ALIASES]
    facts, unavailable = build_macro(series, list(SPECS), as_of)
    assert unavailable == []
    by_id = {f.id: f for f in facts}
    assert len(by_id) == 12
    for alias in ALIASES:
        with closing(macro_sql.load(raw(alias))) as db:
            for transform in ("level", "chg_3m", "yoy"):
                second = getattr(macro_sql, transform)(db, month_end=as_of, as_of=as_of)
                assert by_id[macro.fact_id(alias, transform)].value == second


# ---- 版本时点：晚出版本不用，重述前的日子读旧值 ----


def test_a_vintage_after_the_analysis_date_is_never_used():
    observations = (obs("2026-08-01", 4.4, "2026-09-05"), obs("2026-08-01", 4.1, "2026-10-05"))
    as_of = date(2026, 9, 30)
    assert macro.level(observations, month_end=date(2026, 8, 31), as_of=as_of) == 4.4
    with closing(macro_sql.load(observations)) as db:
        assert macro_sql.level(db, month_end=date(2026, 8, 31), as_of=as_of) == 4.4
    # 合成的 cpi：2026-09-30 读到 8 月的旧值 337.0；9 月的值与 8 月的重述版本都晚于那天
    cpi = raw("cpi")
    assert macro.level(cpi, month_end=date(2026, 9, 30), as_of=date(2026, 9, 30)) == 337.0
    with closing(macro_sql.load(cpi)) as db:
        assert macro_sql.level(db, month_end=date(2026, 9, 30), as_of=date(2026, 9, 30)) == 337.0


def test_a_series_revised_later_shows_the_earlier_value_on_an_earlier_date():
    cpi = raw("cpi")
    month_end = date(2026, 1, 31)
    assert macro.level(cpi, month_end=month_end, as_of=date(2026, 2, 20)) == 329.4
    assert macro.level(cpi, month_end=month_end, as_of=date(2026, 3, 20)) == 329.5
    with closing(macro_sql.load(cpi)) as db:
        assert macro_sql.level(db, month_end=month_end, as_of=date(2026, 2, 20)) == 329.4
        assert macro_sql.level(db, month_end=month_end, as_of=date(2026, 3, 20)) == 329.5


def test_publication_lag_decides_what_the_last_bar_date_sees():
    as_of = date(2026, 9, 30)
    assert macro.level(raw("us10y"), month_end=as_of, as_of=as_of) == 3.85      # 当月可知
    assert macro.level(raw("fed_funds"), month_end=as_of, as_of=as_of) == 3.33  # 只到 8 月


def test_transforms_are_differences_and_none_when_a_side_is_missing():
    as_of = date(2026, 12, 31)
    september = (obs("2026-09-01", 12.0, "2026-10-01"),)
    assert macro.level(september, month_end=date(2026, 9, 30), as_of=as_of) == 12.0
    assert macro.chg_3m(september, month_end=date(2026, 9, 30), as_of=as_of) is None
    assert macro.yoy(september, month_end=date(2026, 9, 30), as_of=as_of) is None
    # 6 月没有观测时，3 个月前那一头落到更早的观测上（5 月）；两头齐了才是差值
    with_may = (*september, obs("2026-05-01", 9.0, "2026-06-01"))
    assert macro.chg_3m(with_may, month_end=date(2026, 9, 30), as_of=as_of) == 3.0
    assert macro.yoy(with_may, month_end=date(2026, 9, 30), as_of=as_of) is None
    with closing(macro_sql.load(with_may)) as db:
        assert macro_sql.chg_3m(db, month_end=date(2026, 9, 30), as_of=as_of) == 3.0
        assert macro_sql.yoy(db, month_end=date(2026, 9, 30), as_of=as_of) is None


# ---- build_macro ----


def test_build_macro_writes_the_twelve_facts_with_ids_units_and_sources():
    as_of = date(2026, 9, 30)
    series = [Series(alias=a, source="合成数据", observations=raw(a)) for a in ALIASES]
    facts, unavailable = build_macro(series, list(SPECS), as_of)
    assert unavailable == []
    by_id = {f.id: f for f in facts}
    assert set(by_id) == {macro.fact_id(a, t) for a in ALIASES for t in ("level", "chg_3m", "yoy")}
    assert by_id["macro_fed_funds"].value == 3.33
    assert by_id["macro_fed_funds"].unit == "%"
    assert by_id["macro_fed_funds"].label == "联邦基金利率"
    assert by_id["macro_cpi"].value == 337.0
    assert by_id["macro_cpi"].unit == "index"
    assert by_id["macro_cpi_chg_3m"].value == pytest.approx(337.0 - 335.1)
    assert by_id["macro_cpi_chg_3m"].label == "消费者价格指数（3 个月变化）"
    assert by_id["macro_cpi_yoy"].value == pytest.approx(337.0 - 325.8)  # 对 2025 年 9 月
    assert by_id["macro_us10y"].value == 3.85
    assert by_id["macro_us10y"].as_of == "2026-09-30"
    for fact in facts:
        assert fact.source == f"macro.{transform_of(fact.id)}"
        assert fact.as_of <= as_of.isoformat()
        assert fact.bars_used == 0
        assert fact.label


def test_build_macro_records_unavailable_with_reasons_before_the_series_starts():
    as_of = date(2025, 3, 31)
    series = [Series(alias=a, source="合成数据", observations=raw(a)) for a in ALIASES]
    facts, unavailable = build_macro(series, list(SPECS), as_of)
    assert {f.id for f in facts} == {f"macro_{a}" for a in ALIASES}
    assert {f.value for f in facts} == {4.33, 318.9, 4.1, 4.23}
    assert {u.id for u in unavailable} == {macro.fact_id(a, t)
                                           for a in ALIASES for t in ("chg_3m", "yoy")}
    assert all(u.reason for u in unavailable)


def test_build_macro_reports_a_missing_series_as_unavailable():
    as_of = date(2026, 9, 30)
    facts, unavailable = build_macro([Series(alias="cpi", source="合成数据",
                                             observations=raw("cpi"))], list(SPECS), as_of)
    assert {f.id for f in facts} == {"macro_cpi", "macro_cpi_chg_3m", "macro_cpi_yoy"}
    assert {u.id for u in unavailable} == {macro.fact_id(a, t)
                                           for a in ("fed_funds", "unemployment", "us10y")
                                           for t in ("level", "chg_3m", "yoy")}
    assert any("没有读到序列 fed_funds" in u.reason for u in unavailable)


# ---- 档案端口 ----


def test_port_filters_observations_and_vintages_by_the_analysis_date():
    s = PORT.series("fed_funds", date(2026, 9, 30))
    assert (s.alias, s.source) == ("fed_funds", "合成数据")
    assert s.observations
    assert all(o.date <= date(2026, 9, 30) and o.realtime_start <= date(2026, 9, 30)
               for o in s.observations)
    assert max(o.date for o in s.observations) == date(2026, 8, 1)   # 9 月的版本日是 10-01


def test_port_returns_the_eligible_rows_in_file_order():
    rows = json.loads((ROOT / "data" / "market" / "synthetic" / "macro" / "cpi.json")
                      .read_text(encoding="utf-8"))["observations"]
    expected = [r["date"] for r in rows
                if r["date"] <= "2026-09-30" and r["realtime_start"] <= "2026-09-30"]
    served = [o.date.isoformat() for o in PORT.series("cpi", date(2026, 9, 30)).observations]
    assert served == expected


def test_missing_file_and_earlier_than_coverage_are_not_covered():
    with pytest.raises(NotCovered):
        PORT.series("gdp", date(2026, 9, 30))
    with pytest.raises(NotCovered):
        PORT.series("fed_funds", date(2024, 12, 31))


def test_covered_but_nothing_published_yet_is_an_empty_series():
    assert PORT.series("fed_funds", date(2025, 1, 15)).observations == ()   # 1 月的值 2 月才发布


def _write_archive(root: Path, name: str, payload: object) -> JsonMacro:
    directory = root / "synthetic" / "macro"
    directory.mkdir(parents=True)
    (directory / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return JsonMacro(root, "synthetic")


def test_malformed_archives_are_source_unavailable(tmp_path: Path):
    good = {"source": "s", "alias": "x", "fred_id": "X",
            "observations": [{"date": "2026-01-01", "value": 1.0, "realtime_start": "2026-02-01"}]}
    with pytest.raises(SourceUnavailable):
        _write_archive(tmp_path, "x", {**good, "extra": 1}).series("x", date(2026, 9, 30))
    with pytest.raises(SourceUnavailable):
        _write_archive(tmp_path / "b", "x", {**good, "alias": "y"}).series("x", date(2026, 9, 30))
    with pytest.raises(SourceUnavailable):
        _write_archive(tmp_path / "c", "x", {**good, "observations": []}).series("x", date(2026, 9, 30))
    undated = {**good, "observations": [{"date": None, "value": 1.0,
                                         "realtime_start": "2026-02-01"}]}
    with pytest.raises(SourceUnavailable):
        _write_archive(tmp_path / "d", "x", undated).series("x", date(2026, 9, 30))
    duplicate = {**good, "observations": [*good["observations"], *good["observations"]]}
    with pytest.raises(SourceUnavailable):
        _write_archive(tmp_path / "e", "x", duplicate).series("x", date(2026, 9, 30))
    broken = tmp_path / "f" / "synthetic" / "macro"
    broken.mkdir(parents=True)
    (broken / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SourceUnavailable):
        JsonMacro(tmp_path / "f", "synthetic").series("x", date(2026, 9, 30))


# ---- 合成数据与生成器 ----


def test_synthetic_archives_meet_the_wp28_requirements():
    for alias in ALIASES:
        rows = json.loads((ROOT / "data" / "market" / "synthetic" / "macro" / f"{alias}.json")
                          .read_text(encoding="utf-8"))["observations"]
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)
        assert dates[0] <= "2025-01-31" and dates[-1] >= "2026-09-01"
    cpi_rows = json.loads((ROOT / "data" / "market" / "synthetic" / "macro" / "cpi.json")
                          .read_text(encoding="utf-8"))["observations"]
    by_period: dict[str, list[dict]] = {}
    for row in cpi_rows:
        by_period.setdefault(row["date"], []).append(row)
    january = by_period["2026-01-01"]
    assert len(january) == 2 and len({r["realtime_start"] for r in january}) == 2 \
        and len({r["value"] for r in january}) == 2                  # 同月两次观测、值不同
    assert any(r["realtime_start"] > "2026-09-30" for r in cpi_rows)  # 有晚于最后 K 线日的版本


def test_generator_writes_the_same_bytes_twice():
    macro_dir = ROOT / "data" / "market" / "synthetic" / "macro"

    def snapshot() -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in sorted(macro_dir.iterdir()) if p.is_file()}

    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    first = snapshot()
    subprocess.run([sys.executable, str(GENERATOR)], check=True, capture_output=True)
    assert snapshot() == first
    assert set(first) == {macro_dir / f"{alias}.json" for alias in ALIASES}
