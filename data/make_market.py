"""合成宇宙生成器：写 data/market/synthetic/。种子固定为 5584，每次运行写出的字节完全相同。

只用标准库；不依赖项目里的任何模块。运行：uv run python data/make_market.py（路径按本文件定位，与工作目录无关）。
业务日是 2025-01-02 到 2026-09-30 的周一到周五。每个序列是带小漂移的收盘价随机游走：
open 在昨收附近，high 不低于 max(open, close)，low 不高于 min(open, close)，价格四舍五入到两位小数。

七个标的（BLUEPRINT 13 / WP2）：
ACME 缓涨的主演示标的；0000.HK 港股；SYNUS、SYNHK 是基准；
THIN 只有最后 40 个业务日；STALE 止于 2026-08-31；
GAPPY 中间缺一周、中间有一行空收盘、最后一行空收盘。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

SEED = 5584
FIRST_DAY = date(2025, 1, 2)
LAST_DAY = date(2026, 9, 30)
STALE_LAST_DAY = date(2026, 8, 31)
THIN_DAYS = 40
GAP_START = 150          # GAPPY 缺的业务日下标：150 到 154，恰一个交易周
GAP_LEN = 5
EMPTY_MID = 250          # GAPPY 中间空收盘那一行的业务日下标
MARKET_DIR = Path(__file__).resolve().parent / "market" / "synthetic"
_COLUMNS = ("date", "open", "high", "low", "close", "volume")

# 每个标的：代码、名称、市场、货币、基准、每手股数、起始价、日漂移、日波动、成交量基数
class _Spec(NamedTuple):
    ticker: str
    name: str
    market: str
    currency: str
    benchmark: str
    lot: int
    start: float
    drift: float
    sigma: float
    vol_base: int


_SPECS: tuple[_Spec, ...] = (
    _Spec("ACME", "Acme Robotics（合成数据）", "US", "USD", "SYNUS", 1, 100.0, 0.0008, 0.015, 100000),
    _Spec("0000.HK", "Zero Holdings（合成数据）", "HK", "HKD", "SYNHK", 100, 30.0, 0.0008, 0.012, 10000),
    _Spec("SYNUS", "Synthetic US Benchmark（合成数据）", "US", "USD", "SPY", 0, 450.0, 0.0006, 0.010, 200000),
    _Spec("SYNHK", "Synthetic HK Benchmark（合成数据）", "HK", "HKD", "^HSI", 0, 24000.0, 0.0004, 0.011, 50000),
    _Spec("THIN", "Thin Trades Ltd.（合成数据）", "US", "USD", "SPY", 0, 12.0, -0.0002, 0.020, 100),
    _Spec("STALE", "Stale Signal Corp.（合成数据）", "US", "USD", "SPY", 0, 80.0, 0.0002, 0.012, 50000),
    _Spec("GAPPY", "Gappy Works（合成数据）", "US", "USD", "SPY", 0, 60.0, 0.0006, 0.014, 80000),
)


def _business_days(first: date, last: date) -> list[date]:
    """从 first 到 last（含）的周一到周五。"""
    days: list[date] = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _walk(rng: random.Random, days: list[date], start: float, drift: float, sigma: float,
          vol_base: int) -> list[tuple[date, float, float, float, float, int]]:
    """收盘价随机游走。四项噪声依次消耗随机数流，顺序固定，保证可复现。"""
    rows: list[tuple[date, float, float, float, float, int]] = []
    prev_close = start
    for day in days:
        open_ = round(prev_close * (1 + rng.gauss(0, 0.004)), 2)
        close = round(open_ * (1 + drift + rng.gauss(0, sigma)), 2)
        high = round(max(open_, close) * (1 + abs(rng.gauss(0, 0.005))), 2)
        low = round(min(open_, close) * (1 - abs(rng.gauss(0, 0.005))), 2)
        rows.append((day, open_, high, low, close, rng.randint(1, 9) * vol_base))
        prev_close = close
    return rows


def _csv_text(rows: list[tuple[date, float, float, float, float, int]],
              empty_close: set[date]) -> str:
    """列 date,open,high,low,close,volume。空收盘的行原样保留一行，close 列为空。"""
    lines = [",".join(_COLUMNS)]
    for day, open_, high, low, close, volume in rows:
        close_text = "" if day in empty_close else f"{close:.2f}"
        lines.append(f"{day.isoformat()},{open_:.2f},{high:.2f},{low:.2f},{close_text},{volume}")
    return "\n".join(lines) + "\n"


def _universe_text() -> str:
    """universe.toml。带点的代码要加引号，否则 TOML 会把它当成嵌套表。"""
    blocks = []
    for spec in _SPECS:
        key = f'"{spec.ticker}"' if "." in spec.ticker else spec.ticker
        blocks.append(
            f"[{key}]\n"
            f'name = "{spec.name}"\n'
            f'market = "{spec.market}"\n'
            f'currency = "{spec.currency}"\n'
            f'benchmark = "{spec.benchmark}"\n'
            f"lot = {spec.lot}\n"
            "synthetic = true\n")
    return "\n".join(blocks)


def main() -> None:
    rng = random.Random(SEED)
    all_days = _business_days(FIRST_DAY, LAST_DAY)
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    for spec in _SPECS:
        ticker, name = spec.ticker, spec.name
        days, empty_close = all_days, set[date]()
        if ticker == "THIN":
            days = all_days[-THIN_DAYS:]
        elif ticker == "STALE":
            days = [day for day in all_days if day <= STALE_LAST_DAY]
        elif ticker == "GAPPY":
            days = [day for i, day in enumerate(all_days)
                    if not GAP_START <= i < GAP_START + GAP_LEN]
            empty_close = {all_days[EMPTY_MID], all_days[-1]}
        rows = _walk(rng, days, spec.start, spec.drift, spec.sigma, spec.vol_base)
        path = MARKET_DIR / f"{ticker.replace('^', '_')}.csv"
        path.write_text(_csv_text(rows, empty_close), encoding="utf-8", newline="\n")
        print(f"已写入 {path.name}（{name}）：{len(rows)} 行，其中空收盘 {len(empty_close)} 行")
    (MARKET_DIR / "universe.toml").write_text(_universe_text(), encoding="utf-8", newline="\n")
    print(f"已写入 universe.toml（{len(_SPECS)} 个标的）→ {MARKET_DIR}")


if __name__ == "__main__":
    main()
