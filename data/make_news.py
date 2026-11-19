"""生成合成新闻数据（BLUEPRINT 19.8、19.12）：data/market/synthetic/news/ 下 ACME.json。
全部是字面量表，没有任何随机性：同一个脚本永远写出同样的字节。表里的无日期行演示
丢弃计数；最后一行晚于最后一根 K 线（2026-09-30），任何分析日为 2026-09-30 的读取
都看不到它。2026-09-23 与 2026-09-30 两天各有头条，演示窗口两端含（默认
[news] window_days = 7 时，2026-09-30 的窗口正是 [2026-09-23, 2026-09-30]）。
"""
from __future__ import annotations

import json
from pathlib import Path

# (published, source, title, url)；published 为 None 表示这条没有日期。
_ACME: tuple[tuple[str | None, str, str, str], ...] = (
    ("2025-03-14", "合成数据", "ACME 完成数据中心冷却系统首批交付", "https://example.com/news/n0001"),
    ("2026-09-18", "合成数据", "ACME 获批参与区域电网升级试点项目", "https://example.com/news/n0002"),
    ("2026-09-23", "合成数据", "ACME 管理层在机构调研中介绍第四季度排产计划", "https://example.com/news/n0003"),
    ("2026-09-24", "合成数据", "ACME 与主要供应商签署长期原料采购协议", "https://example.com/news/n0004"),
    (None, "合成数据", "未注明日期的转载快讯", "https://example.com/news/n0005"),
    ("2026-09-27", "合成数据", "研究机构上调 ACME 评级并调高目标区间", "https://example.com/news/n0006"),
    ("2026-09-30", "合成数据", "ACME 公布社区充电网络二期投资计划", "https://example.com/news/n0007"),
    ("2026-10-03", "合成数据", "ACME 宣布中部仓储扩建项目开工", "https://example.com/news/n0008"),
)


def _file(rows: tuple[tuple[str | None, str, str, str], ...]) -> dict:
    return {
        "source": "合成数据",
        "covers_from": "2025-01-01",
        "covers_to": "2026-12-31",
        "items": [{"published": published, "source": source, "title": title, "url": url}
                  for published, source, title, url in rows],
    }


def main() -> None:
    out_dir = Path(__file__).parent / "market" / "synthetic" / "news"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ACME.json"
    out.write_text(json.dumps(_file(_ACME), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"写出 {out}（{len(_ACME)} 条）")


if __name__ == "__main__":
    main()
