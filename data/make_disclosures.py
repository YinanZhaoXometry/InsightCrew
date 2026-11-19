"""生成合成公告数据（BLUEPRINT 18.7）：data/market/synthetic/disclosures/ 下
ACME.json 与 0000.HK.json。全部是字面量表，没有任何随机性：同一个脚本永远写出
同样的字节。ACME 表里的无日期行演示丢弃计数；最后一行晚于最后一根 K 线（2026-09-30）。
"""
from __future__ import annotations

import json
from pathlib import Path

# (published, kind, title, ref)；published 为 None 表示这条没有日期。
_ACME: tuple[tuple[str | None, str, str, str], ...] = (
    ("2025-02-20", "年度报告", "2024 财年年度报告", "ACME-0001"),
    ("2025-05-08", "季度业绩", "2025 年第一季度业绩公告", "ACME-0002"),
    ("2025-08-07", "季度业绩", "2025 年第二季度业绩公告", "ACME-0003"),
    ("2025-11-06", "季度业绩", "2025 年第三季度业绩公告", "ACME-0004"),
    ("2026-02-19", "年度报告", "2025 财年年度报告", "ACME-0005"),
    ("2026-03-12", "人事变动", "首席财务官辞任及继任安排", "ACME-0006"),
    ("2026-05-07", "季度业绩", "2026 年第一季度业绩公告", "ACME-0007"),
    ("2026-06-18", "重大合同", "与一家物流客户签订三年期供货协议", "ACME-0008"),
    ("2026-08-06", "季度业绩", "2026 年第二季度业绩公告", "ACME-0009"),
    ("2026-08-14", "更正公告", "2025 财年年度报告的更正（重述营业收入与净利润）", "ACME-0010"),
    ("2026-09-22", "股份回购", "董事会批准股份回购计划", "ACME-0011"),
    (None, "其他", "未注明日期的补充说明", "ACME-0012"),
    ("2026-10-15", "业绩预告", "2026 年第三季度业绩预告", "ACME-0013"),
)

# 0000.HK：四份定期公告。2026-06-30 的 90 天窗口内一条也没有，演示 empty 状态。
_0000HK: tuple[tuple[str, str, str, str], ...] = (
    ("2025-03-27", "年度业绩", "2024 年度业绩公告", "0000HK-0001"),
    ("2025-08-28", "中期业绩", "2025 年中期业绩公告", "0000HK-0002"),
    ("2026-03-26", "年度业绩", "2025 年度业绩公告", "0000HK-0003"),
    ("2026-08-27", "中期业绩", "2026 年中期业绩公告", "0000HK-0004"),
)


def _file(rows: tuple[tuple[str | None, str, str, str], ...]) -> dict:
    return {
        "source": "合成数据",
        "covers_from": "2025-01-01",
        "covers_to": "2026-12-31",
        "items": [{"published": published, "kind": kind, "title": title, "ref": ref}
                  for published, kind, title, ref in rows],
    }


def main() -> None:
    out_dir = Path(__file__).parent / "market" / "synthetic" / "disclosures"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("ACME", _ACME), ("0000.HK", _0000HK)):
        out = out_dir / f"{name}.json"
        out.write_text(json.dumps(_file(rows), ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"写出 {out}（{len(rows)} 条）")


if __name__ == "__main__":
    main()
