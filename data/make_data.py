"""生成演示数据集（固定随机种子，结果可复现）。

数据里故意埋了两个坑：
    1) 订单有 coupon（优惠券）和 cancelled（已取消）状态 -> 口径陷阱
    2) 另有一张 refunds 表 -> 第二层口径陷阱，也是最容易被漏掉的
所以同一个问题会算出三个数：
    sum(amount)                      -> 偏高（含未扣优惠券、未扣退款）
    sum(amount - coupon)             -> 仍然偏高（漏了退款）
    sum(amount - coupon) - refunds   -> 正确答案
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta

random.seed(42)

REGIONS = ["华东", "华北", "华南"]
CHANNELS = ["线上", "门店", "分销"]
STATUS = ["paid", "paid", "paid", "shipped", "shipped", "cancelled"]
REASONS = ["质量问题", "七天无理由", "发错货", "价格保护"]

orders, refunds = [], []
oid, rid = 100001, 500001
start = date(2026, 6, 1)

for _ in range(1200):
    d = start + timedelta(days=random.randint(0, 135))       # 2026-06-01 ~ 2026-10-14
    amount = round(random.uniform(180, 9800), 2)
    coupon = round(min(amount * random.uniform(0, 0.12), 900), 2) if random.random() < 0.55 else 0.0
    status = random.choice(STATUS)
    orders.append([f"O{oid}", d.isoformat(), random.choice(REGIONS),
                   random.choice(CHANNELS), amount, coupon, status])
    oid += 1

for o in orders:
    if o[6] == "cancelled":
        continue
    if random.random() < 0.17:
        amt = round((o[4] - o[5]) * random.uniform(0.3, 1.0), 2)
        rdate = (date.fromisoformat(o[1]) + timedelta(days=random.randint(1, 20))).isoformat()
        refunds.append([f"R{rid}", o[0], rdate, amt, random.choice(REASONS)])
        rid += 1

with open("orders.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "order_date", "region", "channel", "amount", "coupon", "status"])
    w.writerows(orders)

with open("refunds.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["refund_id", "order_id", "refund_date", "refund_amount", "reason"])
    w.writerows(refunds)

# ---- 打印三个候选答案，供文档与演示对照 ----
S, E, R = "2026-07-01", "2026-09-30", "华东"
q3 = [o for o in orders if o[2] == R and S <= o[1] <= E and o[6] != "cancelled"]
ref_ids = {o[0] for o in q3}
ref_sum = sum(r[3] for r in refunds if r[1] in ref_ids)

raw = sum(o[4] for o in q3)
after_coupon = sum(o[4] - o[5] for o in q3)
correct = after_coupon - ref_sum

print(f"订单总数        : {len(orders)}")
print(f"退款记录数      : {len(refunds)}")
print(f"Q3 华东有效订单 : {len(q3)}")
print(f"[错误] sum(amount)                = {raw:,.2f}")
print(f"[错误] sum(amount-coupon)         = {after_coupon:,.2f}")
print(f"[正确] sum(amount-coupon)-refund  = {correct:,.2f}   (退款扣减 {ref_sum:,.2f})")
