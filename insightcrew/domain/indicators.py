"""指标的第一条计算路径（Python）。事实账本用它。

每个函数接收按日期升序、且已截止到分析日的序列，返回分析日当天的值；
历史不足时返回 None（绝不返回 0，也不用更短的窗口凑数）。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise


def last(xs: Sequence[float], back: int = 0) -> float | None:
    """倒数第 back+1 个值。back=0 是分析日当天，back=1 是前一个交易日。"""
    return xs[-1 - back] if len(xs) > back else None


def sma(xs: Sequence[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema_series(xs: Sequence[float], n: int) -> list[float]:
    """从第 n 个值开始的 EMA 序列。种子是前 n 个值的简单平均，系数 k = 2/(n+1)。"""
    if len(xs) < n:
        return []
    k, out = 2 / (n + 1), [sum(xs[:n]) / n]
    for x in xs[n:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def ema(xs: Sequence[float], n: int) -> float | None:
    series = ema_series(xs, n)
    return series[-1] if series else None


def _wilder(xs: Sequence[float], n: int) -> float | None:
    """Wilder 平滑：种子是前 n 个值的简单平均，之后 avg = (avg*(n-1) + x) / n。"""
    if len(xs) < n:
        return None
    avg = sum(xs[:n]) / n
    for x in xs[n:]:
        avg = (avg * (n - 1) + x) / n
    return avg


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    changes = [b - a for a, b in pairwise(closes)]
    gain = _wilder([max(c, 0.0) for c in changes], n)
    loss = _wilder([max(-c, 0.0) for c in changes], n)
    if gain is None or loss is None:
        return None
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> float | None:
    trs = [max(high - low, abs(high - prev), abs(low - prev))
           for high, low, prev in zip(highs[1:], lows[1:], closes[:-1], strict=True)]
    return _wilder(trs, n)


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[float, float, float] | None:
    """返回 (macd, signal, hist)。至少需要 slow + signal - 1 根 K 线。"""
    f, s = ema_series(closes, fast), ema_series(closes, slow)
    if not s:
        return None
    line = [a - b for a, b in zip(f[slow - fast:], s, strict=True)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def bollinger(closes: Sequence[float], n: int = 20, width: float = 2.0
              ) -> tuple[float, float, float] | None:
    """返回 (上轨, 中轨, 下轨)。标准差用总体标准差（除以 n）。"""
    if len(closes) < n:
        return None
    window = closes[-n:]
    mid = sum(window) / n
    sd = math.sqrt(sum((x - mid) ** 2 for x in window) / n)
    return mid + width * sd, mid, mid - width * sd


def highest(highs: Sequence[float], n: int) -> float | None:
    return max(highs[-n:]) if len(highs) >= n else None


def lowest(lows: Sequence[float], n: int) -> float | None:
    return min(lows[-n:]) if len(lows) >= n else None


def ret_pct(closes: Sequence[float], n: int) -> float | None:
    """过去 n 根 K 线的收益率，单位是百分比。需要 n+1 个收盘价。"""
    return (closes[-1] / closes[-1 - n] - 1) * 100 if len(closes) > n else None
