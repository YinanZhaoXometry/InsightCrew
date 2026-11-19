"""生成合成宏观数据 data/market/synthetic/macro/<alias>.json（BLUEPRINT 19.8、19.12）。

全部是字面量表，没有任何随机性：同一个脚本永远写出同样的字节。
四个序列（fed_funds、cpi、unemployment、us10y）各写一个文件，观测覆盖 2025-01 到
2026-12（超出 2026-09 的部分：版本日晚于 2026-09-30，任何不晚于那天的分析日都读不到，
正用来证明时点规则）。发布节奏刻意不同：
    fed_funds    观测期＝当月一号，版本＝下月一号（滞后一个月发布）；
    cpi          观测期＝当月一号，版本＝下月 12 日；2026-01 有一次「后来重述」
                 （同一观测期出现两个版本：2026-02-12 的 329.4 与 2026-03-12 的 329.5，
                 中间的分析日读到旧值），2026-08 还有一条 2026-10-13 才生效的重述版本
                 （晚于 2026-09-30，那天永远用不上）；
    unemployment 观测期＝当月一号，版本＝下月 6 日；
    us10y        观测期＝当月最后一天，版本＝当天（收益率当天可知）——于是 2026-09-30
                 的分析读得到 2026-09 的 us10y，而读不到 2026-09 的 fed_funds/cpi/失业率。
"""
from __future__ import annotations

import calendar
import datetime
import json
from pathlib import Path

# 每年的逐月值（1 到 12 月）。fed_funds 与 us10y 是一段降息周期的月均值/月末值。
_FED_FUNDS: dict[int, tuple[float, ...]] = {
    2025: (4.33, 4.33, 4.33, 4.33, 4.33, 4.25, 4.10, 4.02, 3.95, 3.88, 3.82, 3.75),
    2026: (3.70, 3.66, 3.60, 3.55, 3.48, 3.42, 3.38, 3.33, 3.28, 3.22, 3.15, 3.10),
}
_CPI: dict[int, tuple[float, ...]] = {
    2025: (317.5, 318.9, 320.2, 321.6, 322.3, 323.3, 324.1, 324.9, 325.8, 326.6, 327.3, 328.0),
    2026: (329.4, 330.6, 331.9, 333.1, 334.0, 335.1, 336.0, 337.0, 338.1, 339.2, 340.1, 341.0),
}
_UNEMPLOYMENT: dict[int, tuple[float, ...]] = {
    2025: (4.1, 4.1, 4.2, 4.2, 4.2, 4.3, 4.3, 4.4, 4.2, 4.2, 4.1, 4.1),
    2026: (4.0, 4.0, 4.1, 4.1, 4.0, 4.0, 4.1, 4.0, 4.0, 4.1, 4.2, 4.2),
}
_US10Y: dict[int, tuple[float, ...]] = {
    2025: (4.54, 4.24, 4.23, 4.16, 4.40, 4.24, 4.42, 4.36, 4.15, 4.10, 4.10, 4.10),
    2026: (4.08, 4.00, 4.05, 4.02, 3.98, 3.95, 3.96, 3.90, 3.85, 3.80, 3.78, 3.75),
}

# (alias, fred_id) 的对照改编自 TradingAgents 的 vendors/fred.py（BLUEPRINT 19.4）。
_ALIASES: tuple[tuple[str, str], ...] = (
    ("fed_funds", "FEDFUNDS"),
    ("cpi", "CPIAUCSL"),
    ("unemployment", "UNRATE"),
    ("us10y", "DGS10"),
)

# CPI 的两个重述版本：同一观测期、更晚的 realtime_start、不同的值。
# 第一条在 2026-09-30 之前生效（中间的分析日读到旧值）；第二条晚于 2026-09-30 才生效。
_CPI_REVISIONS: tuple[tuple[str, float, str], ...] = (
    ("2026-01-01", 329.5, "2026-03-12"),
    ("2026-08-01", 337.2, "2026-10-13"),
)


def _monthly_lag(years: dict[int, tuple[float, ...]], rt_day: int) -> list[dict]:
    """观测期＝当月一号，realtime_start＝下月 rt_day 号。"""
    rows: list[dict] = []
    for year in sorted(years):
        for i, value in enumerate(years[year]):
            month = i + 1
            rows.append({
                "date": datetime.date(year, month, 1).isoformat(),
                "value": value,
                "realtime_start": datetime.date(year + (month == 12), month % 12 + 1, rt_day).isoformat(),
            })
    return rows


def _monthly_same_day(years: dict[int, tuple[float, ...]]) -> list[dict]:
    """观测期＝当月最后一天，realtime_start＝同一天。"""
    rows: list[dict] = []
    for year in sorted(years):
        for i, value in enumerate(years[year]):
            day = datetime.date(year, i + 1, calendar.monthrange(year, i + 1)[1])
            rows.append({"date": day.isoformat(), "value": value,
                         "realtime_start": day.isoformat()})
    return rows


def _series(alias: str) -> list[dict]:
    """一个别名按它的发布节奏生成全部观测；cpi 追加重述版本后按观测期、版本日排序。"""
    if alias == "fed_funds":
        rows = _monthly_lag(_FED_FUNDS, 1)
    elif alias == "cpi":
        rows = _monthly_lag(_CPI, 12)
    elif alias == "unemployment":
        rows = _monthly_lag(_UNEMPLOYMENT, 6)
    else:
        rows = _monthly_same_day(_US10Y)
    if alias == "cpi":
        rows += [{"date": d, "value": v, "realtime_start": rt} for d, v, rt in _CPI_REVISIONS]
        rows.sort(key=lambda r: (r["date"], r["realtime_start"]))
    return rows


def main() -> None:
    out_dir = Path(__file__).parent / "market" / "synthetic" / "macro"
    out_dir.mkdir(parents=True, exist_ok=True)
    for alias, fred_id in _ALIASES:
        file = {"source": "合成数据", "alias": alias, "fred_id": fred_id,
                "observations": _series(alias)}
        out = out_dir / f"{alias}.json"
        out.write_text(json.dumps(file, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"写出 {out}（{len(file['observations'])} 条观测）")


if __name__ == "__main__":
    main()
