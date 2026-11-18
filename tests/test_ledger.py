"""账本测试：声明、两条路径的一致性与账本本身（蓝图 13 WP3）。

真数据取自 data/market/synthetic/ 的宇宙；缺口与疑似除权没有现成数据，用手工造的 bar 覆盖。
"""
import ast
import inspect
import math
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

from insightcrew.adapters.declarations import load_indicators
from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.domain import indicators as py
from insightcrew.domain import recompute as sql
from insightcrew.domain.ledger import IndicatorSpec, build_ledger, disagreements, recompute_ledger
from insightcrew.domain.market import Bar, Instrument

ROOT = Path(__file__).resolve().parent.parent
INDICATORS_TOML = ROOT / "data" / "indicators.toml"
FAR_FUTURE = date(2099, 1, 1)                 # 取整段序列用：晚于任何数据日期
TICKERS = ("ACME", "0000.HK", "SYNUS", "SYNHK", "THIN", "STALE", "GAPPY")
DATES_PER_INSTRUMENT = 20
COLUMNS = ("open", "high", "low", "close", "volume")


@pytest.fixture(scope="module")
def market() -> CsvMarket:
    return CsvMarket(ROOT / "data" / "market", "synthetic")


@pytest.fixture(scope="module")
def specs() -> tuple[IndicatorSpec, ...]:
    return load_indicators(INDICATORS_TOML)


def _analysis_dates(full: tuple[Bar, ...]) -> list[date]:
    """一段序列里均匀取 20 个分析日（都是序列自己的交易日）。"""
    step = (len(full) - 1) / (DATES_PER_INSTRUMENT - 1)
    return [full[round(i * step)].date for i in range(DATES_PER_INSTRUMENT)]


def _load_db(ticker: str, bars: tuple[Bar, ...]):
    """把整段序列装进路径 2 的内存库；查询里的 CTE 自己按分析日截取。"""
    rows = [(ticker, bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume)
            for bar in bars]
    return closing(sql.load(rows))


def _py_at(spec: IndicatorSpec, columns: dict[str, tuple[float, ...]], count: int):
    """路径 1 的调用约定：fn(*[序列[:count] for inputs], **args)。"""
    return getattr(py, spec.fn)(*(columns[c][:count] for c in spec.inputs), **spec.args)


def _sql_at(spec: IndicatorSpec, db, ticker: str, as_of: str):
    """路径 2 的调用约定：fn(db, ticker, as_of, inputs, **args)。"""
    return getattr(sql, spec.fn)(db, ticker, as_of, spec.inputs, **spec.args)


def _same(a, b) -> None:
    """两个结果一致：None 对 None；多输出按顺序逐一比较（与参考测试同一容差）。"""
    if a is None or b is None:
        assert a is None and b is None, (a, b)
        return
    pairs = zip(a, b, strict=True) if isinstance(a, tuple) else [(a, b)]
    for x, y in pairs:
        assert math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9), (a, b)


# ---- 注册表一致性：一个指标只有同时出现在三个地方才算存在 ----

def test_registry_declares_the_29_facts_of_blueprint_6_4(specs: tuple[IndicatorSpec, ...]) -> None:
    expected = {
        "close", "open", "high", "low", "prev_close", "volume", "avg_volume_20",
        "sma_20", "sma_50", "sma_200", "ema_12", "ema_26", "rsi_14", "atr_14",
        "macd", "macd_signal", "macd_hist", "bb_upper_20", "bb_mid_20", "bb_lower_20",
        "high_20", "high_60", "high_252", "low_20", "low_60", "low_252",
        "ret_5", "ret_20", "ret_60",
    }
    outputs = {output for spec in specs for output in spec.outputs}
    assert outputs == expected and len(outputs) == 29


def test_single_output_takes_its_id_from_the_table_key(specs: tuple[IndicatorSpec, ...]) -> None:
    for spec in specs:
        if spec.key in {"macd", "bollinger_20"}:       # 蓝图 6.4 里仅有的两张多输出表
            assert len(spec.outputs) == 3
        else:
            assert spec.outputs == (spec.key,)
        assert len(spec.labels) == len(spec.outputs)


def test_every_declared_fn_exists_in_both_modules(specs: tuple[IndicatorSpec, ...]) -> None:
    for spec in specs:
        assert callable(getattr(py, spec.fn, None)), f"{spec.key}: indicators 里没有 {spec.fn}"
        assert callable(getattr(sql, spec.fn, None)), f"{spec.key}: recompute 里没有 {spec.fn}"


def _module_functions(module) -> set[str]:
    return {name for name, obj in vars(module).items()
            if inspect.isfunction(obj) and obj.__module__ == module.__name__}


def _call_graph(module) -> dict[str, set[str]]:
    """模块内函数的调用关系：只看对本模块函数的裸名调用。"""
    names = _module_functions(module)
    graph: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.FunctionDef):
            graph[node.name] = {sub.func.id for sub in ast.walk(node)
                                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                                and sub.func.id in names}
    return graph


def _closure(graph: dict[str, set[str]], roots: set[str]) -> set[str]:
    seen = set(roots)
    todo = list(roots)
    while todo:
        for name in graph.get(todo.pop(), ()):
            if name not in seen:
                seen.add(name)
                todo.append(name)
    return seen


def test_every_public_indicator_function_is_reached_from_the_registry(
        specs: tuple[IndicatorSpec, ...]) -> None:
    """indicators.py 的每个公开函数都被至少一个表目用到（直接，或经被用到的函数，如 ema_series）。"""
    used = {spec.fn for spec in specs}
    assert _module_functions(py) <= _closure(_call_graph(py), used)


def test_every_public_recompute_function_is_reached_from_the_registry(
        specs: tuple[IndicatorSpec, ...]) -> None:
    """recompute.py 的每个公开函数都被至少一个表目用到。

    load 不是某个表目的 fn，而是路径 2 的装载器：recompute_ledger 为每个表目调它一次，
    所以一并算作被表目用到（下面的断言把这条白名单钉在真实用法上）。
    """
    assert "recompute.load" in inspect.getsource(recompute_ledger)
    used = {spec.fn for spec in specs} | {"load"}
    assert _module_functions(sql) <= _closure(_call_graph(sql), used)


# ---- 两条路径一致：每个表目 × 每个标的 × 20 个分析日，外加 min_bars 的两侧边界 ----

def _check_boundaries(spec: IndicatorSpec, db, ticker: str,
                      columns: dict[str, tuple[float, ...]], full: tuple[Bar, ...],
                      count: int, as_of: str) -> None:
    """min_bars − 1 根两条路径都给 None；恰好 min_bars 根两条路径都有值且相等。

    路径 2 直接在整段序列的库上查询，把 as_of 取到截取点即可：CTE 只看分析日以前的 bar。
    """
    m, where = spec.min_bars, (ticker, as_of, spec.key)
    if count < m:
        assert _py_at(spec, columns, count) is None, where
        assert _sql_at(spec, db, ticker, as_of) is None, where
        return
    none_day = full[m - 2].date.isoformat() if m >= 2 else (full[0].date - timedelta(days=1)).isoformat()
    value_day = full[m - 1].date.isoformat()
    assert _py_at(spec, columns, m - 1) is None, where
    assert _sql_at(spec, db, ticker, none_day) is None, where
    value = _py_at(spec, columns, m)
    assert value is not None, where
    _same(value, _sql_at(spec, db, ticker, value_day))


@pytest.mark.parametrize("ticker", TICKERS)
def test_paths_agree_for_every_spec_at_20_dates(market: CsvMarket, specs: tuple[IndicatorSpec, ...],
                                                ticker: str) -> None:
    instrument = market.instrument(ticker)
    full = market.history(ticker, FAR_FUTURE)
    columns = {name: tuple(getattr(bar, name) for bar in full) for name in COLUMNS}
    with _load_db(ticker, full) as db:
        for as_of in _analysis_dates(full):
            bars = market.history(ticker, as_of)
            facts, card = build_ledger(instrument, bars, as_of, specs, market.dropped_rows(ticker))
            recomputed = recompute_ledger(instrument, bars, as_of, specs)
            assert disagreements(facts, recomputed) == [], (ticker, as_of.isoformat())
            assert card.bars == len(bars) and card.last_bar == bars[-1].date.isoformat()
            assert card.stale_days == (as_of - bars[-1].date).days
            for spec in specs:
                _check_boundaries(spec, db, ticker, columns, full, len(bars), as_of.isoformat())


# ---- build_ledger ----

def test_build_ledger_acme_2026_09_30(market: CsvMarket, specs: tuple[IndicatorSpec, ...]) -> None:
    instrument = market.instrument("ACME")
    as_of = date(2026, 9, 30)
    bars = market.history("ACME", as_of)
    facts, card = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("ACME"))
    assert len(bars) == 455
    assert len(facts) == 29
    assert len({fact.id for fact in facts}) == 29
    assert {fact.id for fact in facts} == {o for s in specs for o in s.outputs}
    assert all(fact.as_of == "2026-09-30" for fact in facts)   # 最后一根 bar 就是分析日当天
    assert card.first_bar == "2025-01-02" and card.last_bar == "2026-09-30"
    assert card.bars == 455 and card.stale_days == 0 and card.dropped_rows == 0
    assert card.gaps == () and card.warnings == () and card.unavailable == ()
    by_id = {fact.id: fact for fact in facts}
    assert by_id["close"].value == bars[-1].close
    assert by_id["close"].unit == "USD" and by_id["volume"].unit == "shares"
    assert by_id["ret_20"].unit == "%" and by_id["rsi_14"].unit == ""
    assert by_id["close"].source == "indicators.last"
    assert by_id["rsi_14"].source == "indicators.rsi" and by_id["macd"].source == "indicators.macd"
    assert by_id["sma_20"].bars_used == 455


def test_build_ledger_thin_lists_long_windows_unavailable(market: CsvMarket,
                                                          specs: tuple[IndicatorSpec, ...]) -> None:
    instrument = market.instrument("THIN")
    as_of = date(2026, 9, 30)
    bars = market.history("THIN", as_of)
    facts, card = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("THIN"))
    assert len(bars) == 40
    long_ids = {o for s in specs if s.min_bars > len(bars) for o in s.outputs}
    assert "sma_50" in long_ids and "sma_200" in long_ids      # 蓝图点名的两个
    assert long_ids == {"sma_50", "sma_200", "high_60", "high_252", "low_60", "low_252", "ret_60"}
    unavailable = {u.id: u for u in card.unavailable}
    assert set(unavailable) == long_ids
    for uid, item in unavailable.items():
        spec = next(s for s in specs if uid in s.outputs)
        assert item.reason == f"历史不足：需要 {spec.min_bars} 根，现有 40 根"
    emitted = {fact.id for fact in facts}
    assert len(facts) == 22 and not emitted & long_ids
    recomputed = recompute_ledger(instrument, bars, as_of, specs)
    assert all(recomputed[uid] is None for uid in long_ids)
    assert disagreements(facts, recomputed) == []


def test_build_ledger_on_three_bars_emits_only_short_window_facts(
        specs: tuple[IndicatorSpec, ...]) -> None:
    """手工造的 3 根 bar：短窗口的 6 个事实照常产出，其余 23 个输出记为不可用。"""
    instrument = Instrument(ticker="TINY", name="手造标的（测试）", market="US", currency="USD",
                            benchmark="SPY", lot=0, synthetic=True)
    bars = tuple(Bar(date=date(2026, 1, day), open=10.0, high=10.5, low=9.8,
                     close=10.2 + day * 0.1, volume=1000.0) for day in (1, 2, 3))
    as_of = date(2026, 1, 3)
    facts, card = build_ledger(instrument, bars, as_of, specs, dropped_rows=0)
    emitted = {fact.id for fact in facts}
    assert emitted == {"close", "open", "high", "low", "prev_close", "volume"}
    assert card.bars == 3 and card.stale_days == 0 and card.dropped_rows == 0
    assert card.gaps == () and card.warnings == ()
    assert {u.id for u in card.unavailable} == {o for s in specs for o in s.outputs} - emitted
    reason = next(u for u in card.unavailable if u.id == "sma_20").reason
    assert reason == "历史不足：需要 20 根，现有 3 根"
    recomputed = recompute_ledger(instrument, bars, as_of, specs)
    assert recomputed["sma_20"] is None and recomputed["close"] == bars[-1].close
    assert disagreements(facts, recomputed) == []


# ---- 数据卡 ----

def test_data_card_reports_staleness(market: CsvMarket, specs: tuple[IndicatorSpec, ...]) -> None:
    """STALE 序列止于 2026-08-31：向 2026-09-30 要数据，数据卡记 30 个自然日的陈旧。"""
    instrument = market.instrument("STALE")
    as_of = date(2026, 9, 30)
    bars = market.history("STALE", as_of)
    _, card = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("STALE"))
    assert card.last_bar == "2026-08-31" and card.stale_days == 30
    assert card.gaps == () and card.warnings == ()


def test_data_card_reports_dropped_rows_and_the_missing_week(market: CsvMarket,
                                                             specs: tuple[IndicatorSpec, ...]) -> None:
    """GAPPY：丢弃两行（中间一行坏行、末尾一行未结算），中间整整缺一周。"""
    instrument = market.instrument("GAPPY")
    as_of = date(2026, 9, 30)
    bars = market.history("GAPPY", as_of)
    _, card = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("GAPPY"))
    assert card.dropped_rows == 2
    assert card.gaps == ("2025-07-30 到 2025-08-07 之间有 7 个自然日没有 K 线",)
    assert card.last_bar == "2026-09-29" and card.stale_days == 1


def test_gap_threshold_is_more_than_five_calendar_days_without_a_bar(
        specs: tuple[IndicatorSpec, ...]) -> None:
    """空 5 个自然日不算缺口，空 6 个自然日就算（蓝图 6.3 规则 3 的措辞）。"""
    instrument = Instrument(ticker="GAPS", name="手造标的（测试）", market="US", currency="USD",
                            benchmark="SPY", lot=0, synthetic=True)

    def bars(days: tuple[int, ...]) -> tuple[Bar, ...]:
        return tuple(Bar(date=date(2026, 1, d), open=10.0, high=10.5, low=9.8,
                         close=10.2, volume=1000.0) for d in days)

    _, card = build_ledger(instrument, bars((1, 7, 13)), date(2026, 1, 13), specs, dropped_rows=0)
    assert card.gaps == ()
    _, card = build_ledger(instrument, bars((1, 8, 15)), date(2026, 1, 15), specs, dropped_rows=0)
    assert len(card.gaps) == 2


def test_data_card_warns_on_a_one_day_move_over_40_percent(specs: tuple[IndicatorSpec, ...]) -> None:
    """单日跌 50%：数据卡记一条疑似除权/除息的警告，价格仍按原值使用。"""
    instrument = Instrument(ticker="JUMP", name="手造标的（测试）", market="US", currency="HKD",
                            benchmark="SPY", lot=0, synthetic=True)
    bars = (
        Bar(date=date(2026, 3, 2), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        Bar(date=date(2026, 3, 3), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0),
        Bar(date=date(2026, 3, 4), open=50.0, high=51.0, low=49.0, close=50.0, volume=1000.0),
        Bar(date=date(2026, 3, 5), open=50.0, high=51.0, low=49.0, close=50.0, volume=1000.0),
    )
    _, card = build_ledger(instrument, bars, date(2026, 3, 5), specs, dropped_rows=0)
    assert len(card.warnings) == 1
    assert card.warnings[0].startswith("2026-03-04")
    assert "50.0%" in card.warnings[0] and "除权" in card.warnings[0]


# ---- 时点正确 ----

@pytest.mark.parametrize("ticker", TICKERS)
def test_ledger_from_history_equals_ledger_from_hand_cut_series(
        market: CsvMarket, specs: tuple[IndicatorSpec, ...], ticker: str) -> None:
    """时点正确：history(as_of) 的账本与把整段序列手工掐到 as_of 的账本完全相同。"""
    instrument = market.instrument(ticker)
    dropped = market.dropped_rows(ticker)
    full = market.history(ticker, FAR_FUTURE)
    for as_of in _analysis_dates(full):
        from_history = build_ledger(instrument, market.history(ticker, as_of), as_of, specs, dropped)
        cut = tuple(bar for bar in full if bar.date <= as_of)
        from_cut = build_ledger(instrument, cut, as_of, specs, dropped)
        assert from_history == from_cut, (ticker, as_of.isoformat())


# ---- disagreements ----

def test_disagreements_reports_a_0_1_percent_change(market: CsvMarket,
                                                    specs: tuple[IndicatorSpec, ...]) -> None:
    instrument = market.instrument("ACME")
    as_of = date(2026, 9, 30)
    bars = market.history("ACME", as_of)
    facts, _ = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("ACME"))
    recomputed = recompute_ledger(instrument, bars, as_of, specs)
    assert disagreements(facts, recomputed) == []               # 没动过的账本：一致
    tampered = [fact.model_copy(update={"value": fact.value * 1.001}) if fact.id == "sma_20" else fact
                for fact in facts]
    assert disagreements(tampered, recomputed) == ["sma_20"]


def test_disagreements_reports_facts_on_one_path_only(market: CsvMarket,
                                                      specs: tuple[IndicatorSpec, ...]) -> None:
    instrument = market.instrument("ACME")
    as_of = date(2026, 9, 30)
    bars = market.history("ACME", as_of)
    facts, _ = build_ledger(instrument, bars, as_of, specs, market.dropped_rows("ACME"))
    recomputed = recompute_ledger(instrument, bars, as_of, specs)
    missing_one = {k: v for k, v in recomputed.items() if k != "macd"}
    assert disagreements(facts, missing_one) == ["macd"]        # 路径 2 少一个输出
    assert disagreements(facts, {**recomputed, "rsi_14": None}) == ["rsi_14"]   # 路径 2 算不出
    assert disagreements(facts[:-1], recomputed) == [facts[-1].id]              # 路径 1 少一个事实
    assert disagreements(facts, {**recomputed, "ghost": 1.0}) == ["ghost"]      # 路径 2 多出一个键


# ---- 声明校验 ----

_DECLARATION = """[sma_20]
fn = "sma"
inputs = ["close"]
args = { n = 20 }
min_bars = 20
unit = "price"
labels = ["20日简单均线"]
formula = "最近 20 个收盘价的算术平均"
"""


@pytest.mark.parametrize(("name", "text", "field"), [
    ("missing", _DECLARATION.replace("min_bars = 20\n", ""), "min_bars"),
    ("mistyped", _DECLARATION.replace("min_bars = 20", 'min_bars = "20"'), "min_bars"),
    ("list_field", _DECLARATION.replace('inputs = ["close"]', 'inputs = "close"'), "inputs"),
    ("unknown", _DECLARATION + 'bogus = 1\n', "bogus"),
])
def test_declaration_refuses_missing_mistyped_and_unknown_fields(
        tmp_path: Path, name: str, text: str, field: str) -> None:
    path = tmp_path / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="sma_20") as excinfo:
        load_indicators(path)
    message = str(excinfo.value)
    assert field in message and "sma_20" in message             # 点名表和字段


def test_declaration_refuses_a_top_level_value_that_is_not_a_table(tmp_path: Path) -> None:
    path = tmp_path / "scalar.toml"
    path.write_text('noise = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="noise"):
        load_indicators(path)
