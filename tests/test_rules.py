"""规则手册的测试：每条规则在界限上、界内、界外各测一次，并核对 TOML 与评估函数同步。

事实、提案与论点全部手工构造：规则评估是纯函数，不需要行情数据，也不需要账本。
limit 相关的测试都用 tomllib 从 data/rules.toml 读入的 specs，所以同时验证了文件里的默认值。
"""
from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.declarations import load_rules
from insightcrew.domain import rules
from insightcrew.domain.types import Action, Proposal, Rating, RuleResult, RuleSpec, Thesis

_RULES_TOML = Path(__file__).parent.parent / "data" / "rules.toml"

# 蓝图 6.6 的表格：两条 intake 规则，以及带默认 limit 的键和默认值。
INTAKE_KEYS = ("data_fresh", "min_history")
LIMITS = {"data_fresh": 10, "min_history": 60, "max_risk_per_trade_pct": 1.0,
          "max_position_pct": 20, "min_reward_risk": 1.5,
          "min_stop_distance_atr": 1.0, "entry_near_market": 2.0}
FACTS = {"close": 100.0, "atr_14": 5.0}


def load_specs() -> tuple[RuleSpec, ...]:
    """用 tomllib 读规则手册。正式的 load_rules 在 adapters 里，WP4B 才写，测试自己读。"""
    with _RULES_TOML.open("rb") as f:
        tables = tomllib.load(f)
    return tuple(RuleSpec(key=key, limit=table.get("limit"), formula=table["formula"],
                          desc=table["desc"], source=table["source"])
                 for key, table in tables.items())


def make_thesis(rating: Rating = Rating.BUY) -> Thesis:
    return Thesis(rating=rating, horizon_days=10, points=(), invalidation=(), summary="测试用论点")


def make_proposal(action: Action = Action.BUY, entry: float | None = 100.0,
                  stop: float | None = 95.0, target: float | None = 107.5,
                  size: float = 20.0) -> Proposal:
    """干净的 Buy：单笔风险、总仓位、盈亏比、止损距离都恰好落在界限上（蓝图 12.3 的有效变体）。"""
    return Proposal(action=action, entry=entry, stop=stop, target=target, size_pct=size,
                    horizon_days=10, cites=("close",), rationale="测试用提案")


def run(p: Proposal | None = None, thesis: Thesis | None = None,
        facts: dict[str, float] | None = None, specs: tuple[RuleSpec, ...] | None = None,
        position: float = 0.0, cash: float | None = None) -> list[RuleResult]:
    return rules.evaluate(p or make_proposal(), thesis or make_thesis(), facts or FACTS,
                          specs if specs is not None else load_specs(), position, cash)


def by_key(results: list[RuleResult]) -> dict[str, RuleResult]:
    return {r.key: r for r in results}


def spec_of(key: str) -> RuleSpec:
    return {s.key: s for s in load_specs()}[key]


# ---- 结构：结果的个数、顺序，以及 TOML 与评估函数同步 ----


def test_evaluate_returns_the_twelve_proposal_rules_in_blueprint_order():
    results = run()
    assert [r.key for r in results] == list(rules.ORDER)
    assert not ({*INTAKE_KEYS} & {r.key for r in results})


def test_rule_order_matches_the_blueprint_table():
    assert rules.ORDER == (
        "action_matches_rating", "hold_is_flat", "sell_requires_position", "stop_required",
        "stop_side", "size_range", "max_risk_per_trade_pct", "max_position_pct",
        "min_reward_risk", "min_stop_distance_atr", "entry_near_market", "cash_sufficient")


def test_rules_toml_and_evaluators_are_in_sync():
    with _RULES_TOML.open("rb") as f:
        tables = tomllib.load(f)
    assert set(tables) == {*INTAKE_KEYS, *rules.ORDER}
    for key, table in tables.items():
        assert callable(getattr(rules, key, None)), f"规则 {key} 在 rules.py 里没有同名评估函数"
        assert set(table) <= {"limit", "formula", "desc", "source"}
        assert isinstance(table["formula"], str) and table["formula"]
        assert isinstance(table["desc"], str) and table["desc"]
        assert isinstance(table["source"], str) and table["source"]
        assert ("limit" in table) == (key in LIMITS)


def test_rules_toml_limits_are_the_blueprint_defaults():
    limits = {s.key: s.limit for s in load_specs()}
    for key, limit in LIMITS.items():
        assert limits[key] == limit, f"{key} 的默认 limit 应该是 {limit}"
    for key in (*rules.ORDER, *INTAKE_KEYS):
        if key not in LIMITS:
            assert limits[key] is None, f"{key} 不该有默认 limit"


def test_clean_buy_passes_every_applicable_rule():
    status = {r.key: r.status for r in run()}
    assert status == {
        "action_matches_rating": "pass",
        "hold_is_flat": "n/a",
        "sell_requires_position": "n/a",
        "stop_required": "pass",
        "stop_side": "pass",
        "size_range": "pass",
        "max_risk_per_trade_pct": "pass",   # 0.05 × 20 = 1.0，恰好在上限
        "max_position_pct": "pass",         # 20 + 0 = 20，恰好在上限
        "min_reward_risk": "pass",          # 7.5 / 5 = 1.5，恰好在下限
        "min_stop_distance_atr": "pass",    # 5 / 5 = 1.0，恰好在下限
        "entry_near_market": "pass",        # 入场价就是收盘价
        "cash_sufficient": "n/a",
    }


def test_missing_spec_declaration_is_na_not_an_error():
    results = run(specs=())
    assert len(results) == len(rules.ORDER)
    assert all(r.status == "n/a" and "缺少" in r.message for r in results)


# ---- 两条 intake 规则 ----


@pytest.mark.parametrize("days, status", [(9, "pass"), (10, "pass"), (11, "fail")])
def test_data_fresh_at_inside_outside(days: int, status: str):
    last = date(2026, 9, 30)
    r = rules.data_fresh(last, last + timedelta(days=days), spec_of("data_fresh"))
    assert r.status == status
    assert r.limit == 10 and r.actual == float(days)


@pytest.mark.parametrize("bars, status", [(59, "fail"), (60, "pass"), (61, "pass")])
def test_min_history_at_inside_outside(bars: int, status: str):
    r = rules.min_history(bars, spec_of("min_history"))
    assert r.status == status
    assert r.limit == 60 and r.actual == float(bars)


# ---- action_matches_rating：动作必须配得上评级 ----


@pytest.mark.parametrize("action, rating, status", [
    (Action.BUY, Rating.BUY, "pass"),
    (Action.BUY, Rating.OVERWEIGHT, "pass"),
    (Action.BUY, Rating.HOLD, "fail"),          # 蓝图 12.3：Buy under a Hold rating
    (Action.BUY, Rating.UNDERWEIGHT, "fail"),
    (Action.SELL, Rating.UNDERWEIGHT, "pass"),
    (Action.SELL, Rating.SELL, "pass"),
    (Action.SELL, Rating.BUY, "fail"),
    (Action.HOLD, Rating.HOLD, "pass"),
    (Action.HOLD, Rating.SELL, "pass"),         # Hold 配任何评级都行
])
def test_action_matches_rating(action: Action, rating: Rating, status: str):
    r = by_key(run(make_proposal(action=action), make_thesis(rating)))["action_matches_rating"]
    assert r.status == status


# ---- hold_is_flat 与 sell_requires_position ----


def test_hold_is_flat():
    flat = make_proposal(Action.HOLD, None, None, None, 0.0)
    assert by_key(run(flat))["hold_is_flat"].status == "pass"
    sized = make_proposal(Action.HOLD, None, None, None, 5.0)      # 蓝图 12.3：Hold with size 5
    assert by_key(run(sized))["hold_is_flat"].status == "fail"
    leveled = make_proposal(Action.HOLD, 10.0, None, None, 0.0)
    assert by_key(run(leveled))["hold_is_flat"].status == "fail"
    assert by_key(run())["hold_is_flat"].status == "n/a"           # Buy 不适用


def test_sell_requires_position():
    sell = make_proposal(Action.SELL, None, None, None, 30.0)
    assert by_key(run(sell, position=30.0))["sell_requires_position"].status == "pass"  # 恰好在界限
    assert by_key(run(sell, position=50.0))["sell_requires_position"].status == "pass"
    assert by_key(run(sell, position=29.0))["sell_requires_position"].status == "fail"  # 超出持仓
    assert by_key(run(sell, position=0.0))["sell_requires_position"].status == "fail"   # 蓝图 12.3：无持仓
    leveled = make_proposal(Action.SELL, 10.0, None, None, 30.0)
    assert by_key(run(leveled, position=30.0))["sell_requires_position"].status == "fail"
    assert by_key(run())["sell_requires_position"].status == "n/a"                      # Buy 不适用


# ---- 价位与仓位的结构规则 ----


def test_stop_required():
    assert by_key(run())["stop_required"].status == "pass"
    assert by_key(run(make_proposal(stop=None)))["stop_required"].status == "fail"  # 蓝图 12.3：Stop null
    assert by_key(run(make_proposal(stop=0.0)))["stop_required"].status == "fail"
    assert by_key(run(make_proposal(entry=None)))["stop_required"].status == "fail"
    hold = make_proposal(Action.HOLD, None, None, None, 0.0)
    assert by_key(run(hold))["stop_required"].status == "n/a"


def test_stop_side():
    assert by_key(run())["stop_side"].status == "pass"
    assert by_key(run(make_proposal(stop=105.0)))["stop_side"].status == "fail"  # 蓝图 12.3：止损高于入场
    assert by_key(run(make_proposal(stop=100.0)))["stop_side"].status == "fail"  # 等于入场也不行
    assert by_key(run(make_proposal(target=100.0)))["stop_side"].status == "fail"
    assert by_key(run(make_proposal(target=95.0)))["stop_side"].status == "fail"
    no_target = by_key(run(make_proposal(target=None)))
    assert no_target["stop_side"].status == "pass"           # 蓝图 12.3：没有目标价是有效变体
    assert no_target["min_reward_risk"].status == "n/a"


def test_size_range():
    assert by_key(run())["size_range"].status == "pass"
    assert by_key(run(make_proposal(size=100.0)))["size_range"].status == "pass"   # 恰好在界限
    assert by_key(run(make_proposal(size=0.0)))["size_range"].status == "fail"
    assert by_key(run(make_proposal(size=-5.0)))["size_range"].status == "fail"
    assert by_key(run(make_proposal(size=100.5)))["size_range"].status == "fail"


# ---- 五个比率规则：界限上、界内、界外 ----


@pytest.mark.parametrize("size, status", [(20.2, "fail"), (20.0, "pass"), (19.8, "pass")])
def test_max_risk_per_trade_pct(size: float, status: str):
    r = by_key(run(make_proposal(size=size)))["max_risk_per_trade_pct"]
    assert r.status == status and r.limit == 1.0
    assert r.actual == pytest.approx(0.05 * size)


@pytest.mark.parametrize("size, position, status", [(16.0, 5.0, "fail"), (15.0, 5.0, "pass"),
                                                    (14.0, 5.0, "pass")])
def test_max_position_pct(size: float, position: float, status: str):
    r = by_key(run(make_proposal(size=size), position=position))["max_position_pct"]
    assert r.status == status and r.limit == 20
    assert r.actual == pytest.approx(size + position)


@pytest.mark.parametrize("target, status", [(107.0, "fail"), (107.5, "pass"), (108.0, "pass")])
def test_min_reward_risk(target: float, status: str):
    r = by_key(run(make_proposal(target=target)))["min_reward_risk"]
    assert r.status == status and r.limit == 1.5
    assert r.actual == pytest.approx((target - 100.0) / 5.0)


@pytest.mark.parametrize("stop, status", [(98.5, "fail"), (95.0, "pass"), (94.0, "pass")])
def test_min_stop_distance_atr(stop: float, status: str):
    r = by_key(run(make_proposal(stop=stop)))["min_stop_distance_atr"]
    assert r.status == status and r.limit == 1.0
    assert r.actual == pytest.approx((100.0 - stop) / 5.0)


@pytest.mark.parametrize("entry, target, status", [(110.5, 118.0, "fail"), (110.0, 117.5, "pass"),
                                                   (100.0, 107.5, "pass")])
def test_entry_near_market(entry: float, target: float, status: str):
    # entry 抬高后 target 也要抬高，否则 stop_side 会先失败并把这条规则级联成 n/a。
    r = by_key(run(make_proposal(entry=entry, target=target)))["entry_near_market"]
    assert r.status == status and r.limit == 2.0
    assert r.actual == pytest.approx(abs(entry - 100.0) / 5.0)


def test_entry_divided_by_100_is_far_from_the_market():
    # 蓝图 12.3：entry、stop、target 同时除以 100，入场价离市价 19.8 倍 ATR。
    p = make_proposal(entry=1.0, stop=0.95, target=1.075)
    r = by_key(run(p))["entry_near_market"]
    assert r.status == "fail" and r.actual == pytest.approx(19.8)


def test_cash_sufficient_uses_the_callers_cash_pct():
    assert by_key(run(cash=30.0))["cash_sufficient"].status == "pass"
    at_limit = by_key(run(make_proposal(size=30.0), cash=30.0))["cash_sufficient"]
    assert at_limit.status == "pass" and at_limit.limit == 30.0
    assert by_key(run(make_proposal(size=31.0), cash=30.0))["cash_sufficient"].status == "fail"
    assert by_key(run())["cash_sufficient"].status == "n/a"          # 没给现金占比
    hold = make_proposal(Action.HOLD, None, None, None, 0.0)
    assert by_key(run(hold, cash=30.0))["cash_sufficient"].status == "n/a"


# ---- n/a 级联：stop_required 或 stop_side 失败时，五个比率规则一律 n/a ----


def test_stop_side_failure_cascades_to_the_ratio_rules():
    status = {r.key: r.status for r in run(make_proposal(stop=105.0))}
    assert status["stop_side"] == "fail" and status["stop_required"] == "pass"
    for key in rules.RATIO_RULES:
        assert status[key] == "n/a", f"{key} 应该被级联成 n/a"
    assert status["size_range"] == "pass"            # 不依赖止损，不在级联里
    assert status["action_matches_rating"] == "pass"


def test_missing_stop_cascades_to_the_ratio_rules():
    status = {r.key: r.status for r in run(make_proposal(stop=None))}
    assert status["stop_required"] == "fail" and status["stop_side"] == "n/a"
    for key in rules.RATIO_RULES:
        assert status[key] == "n/a", f"{key} 应该被级联成 n/a"


def test_missing_facts_make_the_fact_dependent_rules_na():
    status = {r.key: r.status for r in run(facts={"close": 100.0})}
    assert status["min_stop_distance_atr"] == "n/a"
    assert status["entry_near_market"] == "n/a"


# ---- 1e-9 容差：恰好在界限上算通过，真正越界才算失败 ----


def test_comparisons_allow_1e9_slack():
    inside = by_key(run(make_proposal(size=20.0 + 1e-10)))
    outside = by_key(run(make_proposal(size=20.0 + 1e-8)))
    assert inside["max_position_pct"].status == "pass"
    assert outside["max_position_pct"].status == "fail"


# ---- load_rules（WP4B）：rules.toml 的装载与校验（蓝图 9.5） ----

# 一张合法的规则表，各字段都在契约之内。
_DECLARATION = """[max_risk_per_trade_pct]
limit = 1.0
formula = "(entry − stop) / entry × size_pct"
desc = "止损被触发时的亏损，占权益的百分比"
source = "风控规则 2.1"
"""


def test_load_rules_reads_the_real_rulebook_in_file_order():
    specs = load_rules(_RULES_TOML)
    assert isinstance(specs, tuple)
    assert [s.key for s in specs] == [s.key for s in load_specs()]   # 与 tomllib 手工读入的顺序一致
    for spec, hand in zip(specs, load_specs(), strict=True):
        assert spec == hand                                          # 字段值也一致


def test_load_rules_keeps_the_blueprint_default_limits():
    specs = load_rules(_RULES_TOML)
    limits = {s.key: s.limit for s in specs}
    assert len(specs) == len({*INTAKE_KEYS, *rules.ORDER})           # 14 条规则都在
    for key, limit in LIMITS.items():
        assert limits[key] == limit
    for key in (*rules.ORDER, *INTAKE_KEYS):
        if key not in LIMITS:
            assert limits[key] is None


@pytest.mark.parametrize(("name", "text", "field"), [
    ("missing", _DECLARATION.replace('desc = "止损被触发时的亏损，占权益的百分比"\n', ""), "desc"),
    ("mistyped_limit", _DECLARATION.replace("limit = 1.0", 'limit = "1.0"'), "limit"),
    ("mistyped_formula", _DECLARATION.replace('formula = "(entry − stop) / entry × size_pct"',
                                              "formula = 1"), "formula"),
    ("unknown", _DECLARATION + "bogus = 1\n", "bogus"),
    ("key_in_table", _DECLARATION + 'key = "max_risk_per_trade_pct"\n', "key"),
])
def test_load_rules_refuses_missing_mistyped_and_unknown_fields(
        tmp_path: Path, name: str, text: str, field: str) -> None:
    path = tmp_path / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="max_risk_per_trade_pct") as excinfo:
        load_rules(path)
    message = str(excinfo.value)
    assert field in message and "max_risk_per_trade_pct" in message   # 点名表和字段


def test_load_rules_accepts_an_integer_limit_as_a_float(tmp_path: Path) -> None:
    path = tmp_path / "int_limit.toml"
    path.write_text(_DECLARATION.replace("limit = 1.0", "limit = 1"), encoding="utf-8")
    (spec,) = load_rules(path)
    assert spec.limit == 1.0


def test_load_rules_refuses_a_top_level_value_that_is_not_a_table(tmp_path: Path) -> None:
    path = tmp_path / "scalar.toml"
    path.write_text('noise = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="noise"):
        load_rules(path)
