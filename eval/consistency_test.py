"""口径漂移检查：指标字典（人写的规则）与 L2 实现（SQL）是否对得上。

为什么需要它：
    这套系统的审计可信度，建立在"字典与实现表达同一份口径"之上。
    一旦有人改了 data/metric_dict.json 却没改 tools/metrics.py（或反之），
    审计就会开始给出似是而非的结论——这比直接报错危险得多。

    这个测试把"漂移"变成一条会失败的断言，而不是一个以后才发现的线上 bug。

用法：python -m eval.consistency_test
"""
from __future__ import annotations

import sys

from tools.metric_dict import MetricDict
from tools.metrics import SUPPORTED, consistency_report


def main() -> int:
    md = MetricDict()
    errors, warnings = consistency_report(md)

    print(f"字典版本：{md.doc.get('version')}　条目 {len(md.entries)} 个")
    print(f"L2 已实现：{'、'.join(SUPPORTED)}")
    for e in md.entries:
        mark = "✓" if e["name"] in SUPPORTED else "—"
        rf = len(e.get("required_filters", []))
        print(f"  {mark} {e['name']:6} 单位 {e.get('unit', ''):8} 口径开关 {rf} 项")

    if warnings:
        print("\n已知不支持（系统会明确拒绝，不是漂移）：")
        for w in warnings:
            print("  ·", w)

    if errors:
        print("\n发现口径漂移：")
        for p in errors:
            print("  ✗", p)
        return 1

    print("\n✓ 字典与实现一致，没有漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
