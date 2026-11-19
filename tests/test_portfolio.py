"""组合核心的测试（BLUEPRINT 19.18 WP24）。

验收四态的四种明显情况：没有组合是 not_provided；组合里没有这个标的是 flat；
有是 held；货币不同是 unavailable。held_acme.json 在 ACME 收盘 100 时给出文件
隐含的 position_pct（90 股 × 100 / 10000 权益 × 100 = 90%）。坏文件以 ValueError
拒绝并点名文件。指纹对同一输入永远相同（19.7）。
"""
from __future__ import annotations

import re
import string
from pathlib import Path

import pytest

from insightcrew.adapters.portfolio_json import load_portfolio
from insightcrew.domain.portfolio import Portfolio, PortfolioState, Position, fingerprint, state_for

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIOS = ROOT / "data" / "portfolios"
FLAT_USD = PORTFOLIOS / "flat_usd.json"
HELD_ACME = PORTFOLIOS / "held_acme.json"
LOW_CASH = PORTFOLIOS / "low_cash_usd.json"


def write_bad(tmp_path: Path, text: str) -> Path:
    """把一段坏内容写成临时组合文件，返回它的路径。"""
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    return path


# ---- 三个示例文件 ----


def test_flat_usd_loads_with_cash_equal_to_equity():
    portfolio = load_portfolio(FLAT_USD)
    assert portfolio.currency == "USD"
    assert portfolio.cash == 10000.0 and portfolio.equity == 10000.0    # 空仓：现金即权益
    assert portfolio.positions == ()
    assert isinstance(portfolio.positions, tuple)                       # 序列是元组


def test_held_acme_loads_one_position():
    portfolio = load_portfolio(HELD_ACME)
    assert portfolio.currency == "USD"
    assert portfolio.cash == 1000.0 and portfolio.equity == 10000.0
    assert portfolio.positions == (Position(ticker="ACME", quantity=90.0, average_price=100.0),)


def test_low_cash_loads_with_one_percent_cash():
    portfolio = load_portfolio(LOW_CASH)
    assert portfolio.cash == 100.0 and portfolio.equity == 10000.0
    assert portfolio.positions[0].ticker == "SYNUS"                     # 钱在别的标的上


# ---- state_for 的四态（19.18 WP24 验收）----


def test_no_portfolio_is_not_provided():
    state = state_for(None, "ACME", 100.0, "USD")
    assert state == PortfolioState(state="not_provided", currency=None, quantity=None,
                                   position_pct=None, cash_pct=None,
                                   reason="本次运行没有提供组合")
    assert state.reason                                        # 有理由，不冒充空仓


def test_a_portfolio_without_the_ticker_is_flat():
    portfolio = load_portfolio(FLAT_USD)
    state = state_for(portfolio, "ACME", 100.0, "USD")
    assert state.state == "flat"
    assert state.currency == "USD"
    assert state.quantity == 0.0                               # 知道没持仓：是 0，不是 null
    assert state.position_pct == 0.0
    assert state.cash_pct == 100.0                             # 10000 现金 / 10000 权益
    assert state.reason == ""                                  # flat 时理由为空


def test_a_portfolio_with_the_ticker_is_held():
    state = state_for(load_portfolio(HELD_ACME), "ACME", 100.0, "USD")
    assert state.state == "held"
    assert state.currency == "USD" and state.quantity == 90.0
    assert state.reason == ""                                  # held 时理由为空


def test_held_acme_at_close_100_gives_the_position_pct_the_file_implies():
    # 90 股 × 收盘 100 = 9000 市值；9000 / 10000 权益 × 100 = 90.0%
    state = state_for(load_portfolio(HELD_ACME), "ACME", 100.0, "USD")
    assert state.position_pct == 90.0
    assert state.cash_pct == 10.0                              # 1000 / 10000 × 100


def test_held_position_pct_follows_the_ledger_close_not_the_average_price():
    # 收盘 110：90 × 110 / 10000 × 100 = 99.0%，与平均成本 100 无关
    state = state_for(load_portfolio(HELD_ACME), "ACME", 110.0, "USD")
    assert state.position_pct == 99.0


def test_currency_mismatch_is_unavailable_without_conversion():
    portfolio = Portfolio(currency="HKD", cash=78000.0, equity=78000.0, positions=())
    state = state_for(portfolio, "ACME", 100.0, "USD")
    assert state.state == "unavailable"
    assert state.currency == "HKD"                             # 组合自己的货币
    assert state.quantity is None and state.position_pct is None and state.cash_pct is None
    assert state.reason and "不做换算" in state.reason


def test_low_cash_leaves_one_percent_for_a_buy():
    # ACME 本身是 flat，现金只有 1%：cash_sufficient 会拒绝任何像样的 Buy（19.9 场景）
    state = state_for(load_portfolio(LOW_CASH), "ACME", 100.0, "USD")
    assert state.state == "flat"
    assert state.position_pct == 0.0
    assert state.cash_pct == 1.0                               # 100 / 10000 × 100


def test_ticker_matching_is_exact_on_the_normalized_symbol():
    # 文件里的代码按原样精确比较（见 state_for 的说明）：小写不算持有
    portfolio = Portfolio(currency="USD", cash=1000.0, equity=10000.0,
                          positions=(Position(ticker="acme", quantity=10.0, average_price=100.0),))
    assert state_for(portfolio, "ACME", 100.0, "USD").state == "flat"


def test_the_first_of_repeated_positions_wins():
    portfolio = Portfolio(currency="USD", cash=1000.0, equity=10000.0, positions=(
        Position(ticker="ACME", quantity=10.0, average_price=100.0),
        Position(ticker="ACME", quantity=20.0, average_price=100.0)))
    assert state_for(portfolio, "ACME", 100.0, "USD").quantity == 10.0


# ---- 指纹 ----


def test_fingerprint_of_none_is_none():
    assert fingerprint(None) == "none"


def test_fingerprint_is_deterministic_and_sixteen_hex_chars():
    first = fingerprint(load_portfolio(HELD_ACME))
    again = fingerprint(load_portfolio(HELD_ACME))
    built = fingerprint(Portfolio(currency="USD", cash=1000.0, equity=10000.0,
                                  positions=(Position(ticker="ACME", quantity=90.0,
                                                      average_price=100.0),)))
    assert first == again == built                             # 同一输入，同一指纹
    assert len(first) == 16 and all(c in string.hexdigits for c in first)


def test_fingerprint_changes_when_the_book_changes():
    portfolio = load_portfolio(HELD_ACME)
    changed = portfolio.model_copy(update={"cash": 2000.0})
    assert fingerprint(changed) != fingerprint(portfolio)


# ---- 坏文件：ValueError，点名文件（19.18 WP24 验收）----


def test_malformed_json_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, "{ 不是 JSON")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_unknown_field_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "cash": 1.0, "equity": 1.0,'
                               ' "positions": [], "margin": true}')
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_unknown_position_field_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "cash": 1.0, "equity": 1.0,'
                               ' "positions": [{"ticker": "ACME", "quantity": 1.0,'
                               ' "average_price": 1.0, "stop": 0.5}]}')
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_a_missing_field_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "equity": 1.0, "positions": []}')   # 缺 cash
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_zero_equity_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "cash": 0.0, "equity": 0.0, "positions": []}')
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_negative_equity_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "cash": -1.0, "equity": -1.0, "positions": []}')
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_negative_quantity_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, '{"currency": "USD", "cash": 1.0, "equity": 1.0,'
                               ' "positions": [{"ticker": "ACME", "quantity": -10.0,'
                               ' "average_price": 100.0}]}')
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)


def test_a_non_dictionary_file_is_refused_naming_the_file(tmp_path: Path):
    path = write_bad(tmp_path, "[1, 2, 3]")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_portfolio(path)
