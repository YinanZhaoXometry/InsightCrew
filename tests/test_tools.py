"""工具核心测试（蓝图 19.18 WP26）。直接调用 flows/tools.py，不经内核。

覆盖验收的四条：ACME 最后一根 bar 上 indicator("sma", 20) 与账本的 sma_20 相等；
每个家族在范围两端两条路径一致；超范围与未知名字给以「数据不可用，不要编造。」结尾
的错误说明；分析日之后的 bar 永远不影响结果（正常端口与无视 as_of 的坏端口同答案）。
另有：声明的装载与校验、事实按调用顺序收集（ToolBox.facts）、两条路径分歧时
与账本一样先记 gate.checked 再抛 Halt、工具不写任何黑板键。
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from insightcrew.adapters.declarations import load_indicators, load_rules
from insightcrew.adapters.declarations_tools import load_tool_indicators
from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.adapters.scripted import Script
from insightcrew.domain import indicators as py
from insightcrew.domain import recompute as sql
from insightcrew.domain.ledger import build_ledger
from insightcrew.domain.types import Scope
from insightcrew.flows.deps import Deps
from insightcrew.flows.tools import ToolBox, build_tools
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Halt, Run

ROOT = Path(__file__).resolve().parent.parent
SPECS = load_tool_indicators(ROOT / "data" / "tool_indicators.toml")
BY_KEY = {spec.key: spec for spec in SPECS}
AS_OF = date(2026, 9, 30)                 # ACME 的最后一根 bar
MID_AS_OF = "2026-06-30"                  # 序列中段：钳制证明用的分析日
FAR_FUTURE = date(2099, 1, 1)
END = "数据不可用，不要编造。"
UNITS = {"sma": "USD", "ema": "USD", "rsi": "", "atr": "USD", "ret_pct": "%",
         "highest": "USD", "lowest": "USD"}


@pytest.fixture(scope="module")
def market() -> CsvMarket:
    return CsvMarket(ROOT / "data" / "market", "synthetic")


@pytest.fixture(scope="module")
def deps() -> Deps:
    return Deps(market=CsvMarket(ROOT / "data" / "market", "synthetic"),
                indicators=load_indicators(ROOT / "data" / "indicators.toml"),
                rules=load_rules(ROOT / "data" / "rules.toml"),
                today=AS_OF, universe="synthetic")


def _run_for(ticker: str, as_of: str) -> Run:
    """一个只带 request/scope 的运行：工具在 intake 之后、任何模型调用之前构建。"""
    journal = Journal()
    run = Run(id="test", journal=journal, board=Board(journal), models=Script({}), budget=Budget())
    run.board.put("request/scope", Scope(ticker=ticker, as_of=as_of, universe="synthetic",
                                         requested=ticker), by="intake")
    return run


@dataclass
class _LeakyMarket:
    """坏端口：无视 as_of，把整段序列（含分析日之后的 bar）原样交出去。"""

    inner: CsvMarket

    def instrument(self, ticker: str):
        return self.inner.instrument(ticker)

    def history(self, ticker: str, as_of: date):
        return self.inner.history(ticker, FAR_FUTURE)

    def dropped_rows(self, ticker: str) -> int:
        return self.inner.dropped_rows(ticker)


def _leaky_deps(market: CsvMarket) -> Deps:
    return Deps(market=_LeakyMarket(market), indicators=(), rules=(), today=AS_OF,
                universe="synthetic")


# ---- 声明的装载 ----

def test_loader_reads_the_seven_families_with_the_pinned_ranges() -> None:
    assert [(s.key, s.min, s.max) for s in SPECS] == [
        ("sma", 2, 250), ("ema", 2, 250), ("rsi", 2, 60), ("atr", 2, 60),
        ("ret_pct", 1, 250), ("highest", 2, 252), ("lowest", 2, 252)]


def test_every_family_names_a_function_both_paths_implement() -> None:
    for spec in SPECS:
        assert callable(getattr(py, spec.fn, None)), f"{spec.key}: indicators 里没有 {spec.fn}"
        assert callable(getattr(sql, spec.fn, None)), f"{spec.key}: recompute 里没有 {spec.fn}"
        assert "{n}" in spec.label and spec.arg == "n"


_TABLE = """[sma]
fn = "sma"
inputs = ["close"]
arg = "n"
min = 2
max = 250
unit = "price"
label = "{n}日简单均线"
"""


@pytest.mark.parametrize(("name", "text", "needle"), [
    ("missing", _TABLE.replace("max = 250\n", ""), "max"),
    ("mistyped", _TABLE.replace("min = 2", 'min = "2"'), "min"),
    ("unknown", _TABLE + "bogus = 1\n", "bogus"),
    ("bad_range", _TABLE.replace("min = 2", "min = 300"), "范围"),
    ("no_placeholder", _TABLE.replace('label = "{n}日简单均线"', 'label = "日简单均线"'), "占位符"),
])
def test_loader_refuses_broken_tables(tmp_path: Path, name: str, text: str, needle: str) -> None:
    path = tmp_path / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="sma") as excinfo:
        load_tool_indicators(path)
    assert needle in str(excinfo.value)                       # 点名表和问题所在


def test_loader_refuses_a_top_level_value_that_is_not_a_table(tmp_path: Path) -> None:
    path = tmp_path / "scalar.toml"
    path.write_text('noise = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="noise"):
        load_tool_indicators(path)


# ---- 验收一：ACME 最后一根 bar 上，indicator("sma", 20) 与账本的 sma_20 相等 ----

def test_indicator_sma_20_equals_the_ledger_sma_20(market: CsvMarket, deps: Deps) -> None:
    tools = build_tools(_run_for("ACME", "2026-09-30"), deps, SPECS)
    instrument = market.instrument("ACME")
    bars = market.history("ACME", AS_OF)
    facts, _ = build_ledger(instrument, bars, AS_OF, deps.indicators,
                            market.dropped_rows("ACME"))
    ledger_sma_20 = next(f for f in facts if f.id == "sma_20")
    result = tools["indicator"]("sma", 20)
    assert result == {"id": "sma_20", "value": ledger_sma_20.value,
                      "unit": "USD", "as_of": "2026-09-30"}


# ---- 验收二：每个家族在范围两端，两条路径一致 ----

@pytest.mark.parametrize("key", list(BY_KEY))
@pytest.mark.parametrize("edge", ["min", "max"])
def test_every_family_at_both_range_ends_agrees_across_paths(
        market: CsvMarket, deps: Deps, key: str, edge: str) -> None:
    spec = BY_KEY[key]
    n = getattr(spec, edge)
    result = build_tools(_run_for("ACME", "2026-09-30"), deps, SPECS)["indicator"](key, n)
    assert isinstance(result, dict), (key, n, result)
    bars = market.history("ACME", AS_OF)
    rows = [("ACME", b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in bars]
    one = getattr(py, spec.fn)(*(tuple(getattr(b, c) for b in bars) for c in spec.inputs),
                               **{spec.arg: n})
    with closing(sql.load(rows)) as db:
        two = getattr(sql, spec.fn)(db, "ACME", "2026-09-30", spec.inputs, **{spec.arg: n})
    assert one is not None and two is not None
    assert result["id"] == f"{key}_{n}"
    assert result["value"] == one                                  # 与路径 1 逐位相同
    assert result["value"] == pytest.approx(two, rel=1e-9, abs=1e-9)   # 与路径 2 容差内一致
    assert result["unit"] == UNITS[key] and result["as_of"] == "2026-09-30"


# ---- 验收三：超范围与未知名字给错误说明 ----

@pytest.mark.parametrize(("name", "n"), [
    ("sma", 1), ("sma", 251), ("ema", 1), ("ema", 251),
    ("rsi", 1), ("rsi", 61), ("atr", 1), ("atr", 61),
    ("ret_pct", 0), ("ret_pct", 251),
    ("highest", 1), ("highest", 253), ("lowest", 1), ("lowest", 253),
])
def test_out_of_range_windows_give_the_error_string(deps: Deps, name: str, n: int) -> None:
    result = build_tools(_run_for("ACME", "2026-09-30"), deps, SPECS)["indicator"](name, n)
    assert isinstance(result, str) and result.endswith(END), (name, n, result)


def test_unknown_names_give_the_error_string(deps: Deps) -> None:
    tools = build_tools(_run_for("ACME", "2026-09-30"), deps, SPECS)
    result = tools["indicator"]("macd", 12)          # macd 在账本里，但不是工具家族
    assert isinstance(result, str) and result.endswith(END) and "macd" in result


def test_insufficient_history_gives_the_error_string(deps: Deps) -> None:
    tools = build_tools(_run_for("THIN", "2026-09-30"), deps, SPECS)   # THIN 只有 40 根
    result = tools["indicator"]("highest", 60)
    assert isinstance(result, str) and result.endswith(END) and "40" in result


# ---- 验收四：分析日之后的 bar 永远不影响结果 ----

def test_a_bar_after_the_analysis_date_never_changes_a_result(market: CsvMarket,
                                                              deps: Deps) -> None:
    """正常端口按 as_of 截断；坏端口把分析日之后的 bar 也交出来。两者的答案必须相同。"""
    cut = build_tools(_run_for("ACME", MID_AS_OF), deps, SPECS)
    leaky = build_tools(_run_for("ACME", MID_AS_OF), _leaky_deps(market), SPECS)
    for spec in SPECS:
        for n in (spec.min, spec.max):
            a, b = cut["indicator"](spec.key, n), leaky["indicator"](spec.key, n)
            assert isinstance(a, dict) and isinstance(b, dict), (spec.key, n, a, b)
            assert a == b and a["as_of"] <= MID_AS_OF      # as_of 是钳制后最后一根 bar 的日期


# ---- 事实的收集与黑板的干净 ----

def test_facts_are_collected_in_call_order_without_duplicates(deps: Deps) -> None:
    run = _run_for("ACME", "2026-09-30")
    tools = build_tools(run, deps, SPECS)
    assert isinstance(tools, ToolBox)
    first = tools["indicator"]("sma", 20)
    second = tools["indicator"]("rsi", 14)
    again = tools["indicator"]("sma", 20)
    assert again == first                                        # 重复调用同答案
    assert [f.id for f in tools.facts] == ["sma_20", "rsi_14"]     # 按调用顺序、不重复（板键存完整 Fact）
    assert [f.value for f in tools.facts] == [first["value"], second["value"]]
    refused = tools["indicator"]("sma", 999)                    # 错误说明不进事实列表
    assert isinstance(refused, str) and refused.endswith(END)
    assert [f.id for f in tools.facts] == ["sma_20", "rsi_14"]
    assert set(run.board.snapshot()) == {"request/scope"}        # 工具不写任何黑板键


def test_the_tool_description_names_every_family_and_range(deps: Deps) -> None:
    tools = build_tools(_run_for("ACME", "2026-09-30"), deps, SPECS)
    doc = tools["indicator"].__doc__ or ""
    for spec in SPECS:
        assert spec.key in doc and str(spec.min) in doc and str(spec.max) in doc
    assert "不接收日期" in doc and END in doc


# ---- 两条路径分歧：与账本一样停机 ----

def test_a_disagreement_between_the_paths_halts_the_run(deps: Deps,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run_for("ACME", "2026-09-30")
    tools = build_tools(run, deps, SPECS)
    assert isinstance(tools, ToolBox)
    monkeypatch.setattr(py, "sma", lambda xs, n: 1.0)                    # 路径 1 被篡改
    with pytest.raises(Halt) as halt:
        tools["indicator"]("sma", 20)
    assert halt.value.reason == "ledger_inconsistent" and halt.value.detail == "sma_20"
    gates = [e for e in run.journal.events if e.type == "gate.checked"]
    assert len(gates) == 1                                      # 拦下之前先记下细节
    assert gates[0].actor == "tools" and gates[0].data["gate"] == "tools"
    assert gates[0].data["findings"] == 1
    assert gates[0].data["detail"] == [{"check": "ledger_inconsistent", "subject": "sma_20",
                                        "message": "两条计算路径给出的结果不一致"}]
    assert tools.facts == []                                    # 分歧的事实不收集
    assert set(run.board.snapshot()) == {"request/scope"}


def test_build_tools_needs_the_scope_on_the_board(deps: Deps) -> None:
    journal = Journal()
    run = Run(id="test", journal=journal, board=Board(journal), models=Script({}), budget=Budget())
    with pytest.raises(ValueError, match="request/scope"):
        build_tools(run, deps, SPECS)
