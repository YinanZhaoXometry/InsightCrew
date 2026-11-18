"""两条计算路径（Python 与 SQL）必须在每一个分析日给出同样的数。"""
import math
import random
from contextlib import closing
from datetime import date, timedelta

import pytest

from insightcrew.domain import indicators as py
from insightcrew.domain import recompute as sql

CUTS = (5, 14, 15, 16, 20, 26, 33, 34, 35, 60, 200, 201, 300)


def walk(n: int, seed: int) -> list[tuple]:
    rng, rows, close, day = random.Random(seed), [], 100.0, date(2025, 1, 1)
    while len(rows) < n:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        open_ = close * (1 + rng.gauss(0, 0.004))
        close = open_ * (1 + rng.gauss(0.0003, 0.015))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.005)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.005)))
        rows.append(("ACME", day.isoformat(), round(open_, 2), round(high, 2), round(low, 2),
                     round(close, 2), float(rng.randint(1, 9) * 100000)))
    return rows


def same(a, b) -> None:
    if a is None or b is None:
        assert a is None and b is None, (a, b)
        return
    pairs = zip(a, b, strict=True) if isinstance(a, tuple) else [(a, b)]
    for x, y in pairs:
        assert math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9), (x, y)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_python_and_sql_agree_at_every_as_of(seed):
    rows = walk(300, seed)
    with closing(sql.load(rows + [("OTHER", r[1], 1, 1, 1, 1, 1) for r in rows])) as db:
        for cut in CUTS:
            _compare(db, rows[:cut])


def _compare(db, part: list[tuple]) -> None:
    day = part[-1][1]
    cut = len(part)
    hi, lo, cl, vo = ([r[i] for r in part] for i in (3, 4, 5, 6))

    def q(fn, inputs, **kw):
        return getattr(sql, fn)(db, "ACME", day, inputs, **kw)

    same(py.last(cl), q("last", ["close"]))
    same(py.last(cl, 1), q("last", ["close"], back=1))
    same(py.last(cl, cut), q("last", ["close"], back=cut))
    same(py.sma(cl, 20), q("sma", ["close"], n=20))
    same(py.sma(cl, 200), q("sma", ["close"], n=200))
    same(py.sma(vo, 20), q("sma", ["volume"], n=20))
    same(py.ema(cl, 12), q("ema", ["close"], n=12))
    same(py.rsi(cl, 14), q("rsi", ["close"], n=14))
    same(py.atr(hi, lo, cl, 14), q("atr", ["high", "low", "close"], n=14))
    same(py.macd(cl), q("macd", ["close"], fast=12, slow=26, signal=9))
    same(py.bollinger(cl), q("bollinger", ["close"], n=20, width=2.0))
    same(py.highest(hi, 20), q("highest", ["high"], n=20))
    same(py.lowest(lo, 20), q("lowest", ["low"], n=20))
    same(py.ret_pct(cl, 20), q("ret_pct", ["close"], n=20))


def test_future_bars_change_nothing():
    rows = walk(300, 5)
    day = rows[199][1]
    with closing(sql.load(rows)) as with_future, closing(sql.load(rows[:200])) as without:
        _same_with_and_without_future(with_future, without, day)


def _same_with_and_without_future(with_future, without, day: str) -> None:
    for fn, inputs, kw in (("ema", ["close"], {"n": 26}), ("rsi", ["close"], {"n": 14}),
                           ("atr", ["high", "low", "close"], {"n": 14}), ("macd", ["close"], {}),
                           ("highest", ["high"], {"n": 60}), ("ret_pct", ["close"], {"n": 60})):
        assert getattr(sql, fn)(with_future, "ACME", day, inputs, **kw) == \
            getattr(sql, fn)(without, "ACME", day, inputs, **kw)


def test_unknown_column_is_rejected():
    with closing(sql.load([])) as db, pytest.raises(ValueError):
        sql.sma(db, "ACME", "2025-01-01", ["close; DROP TABLE bars"], n=5)


def test_min_history_boundaries():
    cl = [r[5] for r in walk(40, 7)]
    assert py.rsi(cl[:14]) is None and py.rsi(cl[:15]) is not None
    assert py.macd(cl[:33]) is None and py.macd(cl[:34]) is not None
    assert py.ret_pct(cl[:20], 20) is None and py.ret_pct(cl[:21], 20) is not None


def test_flat_series_rsi_is_100_not_error():
    rows = [("F", f"2025-01-{i:02d}", 10, 10, 10, 10, 1.0) for i in range(1, 29)]
    assert py.rsi([10.0] * 28) == 100.0
    with closing(sql.load(rows)) as db:
        assert sql.rsi(db, "F", "2025-01-28", ["close"]) == 100.0
