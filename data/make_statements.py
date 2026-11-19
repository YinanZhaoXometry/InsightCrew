"""生成合成年报数据 data/market/synthetic/statements/ACME.json（BLUEPRINT 18.7）。

全部是字面量表，没有任何随机性：同一个脚本永远写出同样的字节。
0000.HK 故意没有文件（基本面通道对它不可用）；其余合成标的不做证据。
第四行是重述：2026-05-29 的运行读到 1105（百万），2026-09-30 的运行读到 1081 并在
数据卡上看到重述提醒；第五行的申报日晚于最后一根 K 线，任何更早的分析日都读不到。
"""
from __future__ import annotations

import json
from pathlib import Path

# (concept, period_end, period_days, value, filed, form)；金额为申报货币的原单位。
_ROWS: tuple[tuple[str, str, int, float, str, str], ...] = (
    # 2023 财年（2024-02-22 申报）
    ("revenue", "2023-12-31", 365, 820_000_000, "2024-02-22", "10-K"),
    ("net_income", "2023-12-31", 365, 61_000_000, "2024-02-22", "10-K"),
    ("eps_diluted", "2023-12-31", 365, 1.22, "2024-02-22", "10-K"),
    ("operating_cash_flow", "2023-12-31", 365, 95_000_000, "2024-02-22", "10-K"),
    ("total_assets", "2023-12-31", 0, 1_310_000_000, "2024-02-22", "10-K"),
    ("total_liabilities", "2023-12-31", 0, 590_000_000, "2024-02-22", "10-K"),
    # 2024 财年（2025-02-20 申报）
    ("revenue", "2024-12-31", 365, 940_000_000, "2025-02-20", "10-K"),
    ("net_income", "2024-12-31", 365, 78_000_000, "2025-02-20", "10-K"),
    ("eps_diluted", "2024-12-31", 365, 1.55, "2025-02-20", "10-K"),
    ("operating_cash_flow", "2024-12-31", 365, 120_000_000, "2025-02-20", "10-K"),
    ("total_assets", "2024-12-31", 0, 1_480_000_000, "2025-02-20", "10-K"),
    ("total_liabilities", "2024-12-31", 0, 640_000_000, "2025-02-20", "10-K"),
    # 2025 财年（2026-02-19 申报）
    ("revenue", "2025-12-31", 365, 1_105_000_000, "2026-02-19", "10-K"),
    ("net_income", "2025-12-31", 365, 97_000_000, "2026-02-19", "10-K"),
    ("eps_diluted", "2025-12-31", 365, 1.92, "2026-02-19", "10-K"),
    ("operating_cash_flow", "2025-12-31", 365, 141_000_000, "2026-02-19", "10-K"),
    ("total_assets", "2025-12-31", 0, 1_690_000_000, "2026-02-19", "10-K"),
    ("total_liabilities", "2025-12-31", 0, 705_000_000, "2026-02-19", "10-K"),
    # 2025 财年的重述（2026-08-14 申报）：只有收入、净利润与每股收益重新申报
    ("revenue", "2025-12-31", 365, 1_081_000_000, "2026-08-14", "10-K/A"),
    ("net_income", "2025-12-31", 365, 90_000_000, "2026-08-14", "10-K/A"),
    ("eps_diluted", "2025-12-31", 365, 1.78, "2026-08-14", "10-K/A"),
    # 2025 财年的再次重述（2026-11-05 申报）：申报日晚于最后一根 K 线（2026-09-30），
    # 任何更早的分析日都读不到；只有收入重新申报。
    ("revenue", "2025-12-31", 365, 1_090_000_000, "2026-11-05", "10-K/A"),
)

_FILE = {
    "source": "合成数据",
    "currency": "USD",
    "covers_to": "2026-12-31",
    "rows": [{"concept": c, "period_end": pe, "period_days": pd, "value": v,
              "filed": f, "form": form}
             for c, pe, pd, v, f, form in _ROWS],
}


def main() -> None:
    out = Path(__file__).parent / "market" / "synthetic" / "statements" / "ACME.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_FILE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写出 {out}（{len(_FILE['rows'])} 行）")


if __name__ == "__main__":
    main()
