"""生成合成社交数据（BLUEPRINT 19.8、19.12）：data/market/synthetic/social/ 下 ACME.json。
全部是字面量表，没有任何随机性：同一个脚本永远写出同样的字节。表里的无日期行演示
丢弃计数；最后一行晚于最后一根 K 线（2026-09-30），任何分析日为 2026-09-30 的读取
都看不到它。2026-09-23 与 2026-09-30 两天各有帖子，演示窗口两端含（默认
[sentiment] window_days = 7 时，2026-09-30 的窗口正是 [2026-09-23, 2026-09-30]）。
tag 是来源自带的标签（bullish、bearish、none），正文都不超过 280 字。
"""
from __future__ import annotations

import json
from pathlib import Path

# (published, source, text, url, tag)；published 为 None 表示这条没有日期。
_ACME: tuple[tuple[str | None, str, str, str, str], ...] = (
    ("2026-09-21", "合成数据", "ACME 今天放量突破，趋势走强，继续持有", "https://example.com/social/s0001", "bullish"),
    ("2026-09-23", "合成数据", "看了采购协议的新闻，成本端这一下应该稳了", "https://example.com/social/s0002", "none"),
    ("2026-09-25", "合成数据", "ACME 涨得有点急，我减了一半仓位落袋", "https://example.com/social/s0003", "bearish"),
    (None, "合成数据", "未注明日期的跟帖", "https://example.com/social/s0004", "none"),
    ("2026-09-28", "合成数据", "论坛都在传 ACME 要扩产，公告还没见到", "https://example.com/social/s0005", "bullish"),
    ("2026-09-30", "合成数据", "季末收官，ACME 这个月的走势比大盘强一截", "https://example.com/social/s0006", "none"),
    ("2026-10-02", "合成数据", "十月开门红，ACME 高开高走", "https://example.com/social/s0007", "bullish"),
)


def _file(rows: tuple[tuple[str | None, str, str, str, str], ...]) -> dict:
    return {
        "source": "合成数据",
        "covers_from": "2025-01-01",
        "covers_to": "2026-12-31",
        "posts": [{"published": published, "source": source, "text": text, "url": url, "tag": tag}
                  for published, source, text, url, tag in rows],
    }


def main() -> None:
    out_dir = Path(__file__).parent / "market" / "synthetic" / "social"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ACME.json"
    out.write_text(json.dumps(_file(_ACME), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"写出 {out}（{len(_ACME)} 条）")


if __name__ == "__main__":
    main()
