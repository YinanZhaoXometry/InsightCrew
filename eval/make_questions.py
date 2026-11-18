"""生成评估集：12 道题 + 参考答案。

参考答案是**用 SQL 独立算出来的**（l2_value），不是模型生成的。
这一点很重要：评估集的答案必须来自可信来源，否则你是在拿模型的结果
去评判模型的结果，消融实验会变成自证。

使用前请人工复核一遍（挑 2~3 题手算），这是学术诚信的底线。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.data_tools import l2_value  # noqa: E402

TPL = {"region": "华东", "start": "2026-07-01", "end": "2026-09-30",
       "exclude_cancelled": True, "coupon_deduction": True, "subtract_refund": True}

CASES = [
    ("Q01", "简单汇总", "2026 年第三季度华东区的营收是多少？", {**TPL}),
    ("Q02", "简单汇总", "2026 年第三季度华北区的营收是多少？", {**TPL, "region": "华北"}),
    ("Q03", "简单汇总", "2026 年第三季度全公司营收是多少？", {**TPL, "region": None}),
    ("Q04", "简单汇总", "2026 年 8 月华南区的营收是多少？",
     {**TPL, "region": "华南", "start": "2026-08-01", "end": "2026-08-31"}),
    ("Q05", "简单汇总", "2026 年 9 月的营收是多少？",
     {**TPL, "region": None, "start": "2026-09-01", "end": "2026-09-30"}),
    ("Q06", "口径陷阱", "2026 年第三季度华东区的净营收是多少？（需扣退款）", {**TPL}),
    ("Q07", "口径陷阱", "2026 年第三季度华东区的销售额是多少？（按财务口径）", {**TPL}),
    ("Q08", "口径陷阱", "2026 年第三季度华东区的营业收入是多少？（口径手册 v2.3）", {**TPL}),
    ("Q09", "聚合层级", "2026 年第三季度华东区的营收是多少？（按季度汇总，不要按月相加错）", {**TPL}),
    ("Q10", "数据质量", "2026 年第二季度华东区的营收是多少？",
     {**TPL, "start": "2026-04-01", "end": "2026-06-30"}),
    ("Q11", "不可答", "2026 年第三季度华东区的毛利率是多少？", None),
    ("Q12", "不可答", "2026 年第三季度华东区的营销费用是多少？", None),
]


def main():
    rows = []
    for qid, cat, q, f in CASES:
        ref = None
        note = ""
        if f is not None:
            ref = l2_value(f)
        else:
            note = "数据集中无该字段，正确答案是'数据不支持'，不允许估算"
        if qid == "Q10":
            note = "数据集从 2026-06-01 开始，Q2 仅覆盖 6 月一个月，需在结论中声明覆盖不全"
        rows.append({"id": qid, "category": cat, "question": q, "filters": f,
                     "expected": ref, "note": note})

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "questions.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"已写入 {out}（{len(rows)} 题）")
    for r in rows:
        e = "—" if r["expected"] is None else f"{r['expected']:,.2f}"
        print(f"  {r['id']} [{r['category']}] {e}")


if __name__ == "__main__":
    main()
