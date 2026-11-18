"""离线剧本：没有 API key 时，让整条流水线仍然能跑完。

为什么值得单独写这个文件：
    1) 你的编排、沙箱、审计逻辑对不对，与"模型聪不聪明"是两件事。
       先把模型的不确定性摘掉，才能验证机制本身。
    2) 录 demo 时不怕网络抖动、不怕限流。
    3) 三个候选答案（118 万 / 115 万 / 103 万）是钉死的，
       答辩现场可以随时复现，不会因为模型随机性变成另一套说法。

真实使用时应换成 LLM，这个文件只服务演示与回归测试。
"""
from __future__ import annotations

import re

from core.question_parser import describe, parse


def _q(prompt: str) -> str:
    """从 prompt 里取出真正的用户问题。

    坑：直接把整个 prompt 交给解析器，会把画像卡里的
    "region 取值含华东/华北/华南" 也当成提问内容，
    地区就被解析成三个区（等于全国）。
    所以只认第一行 `用户问题：...`。
    """
    m = re.search(r"用户问题[:：]\s*([^\n]+)", prompt or "")
    return m.group(1).strip() if m else (prompt or "")


def profiler(i, user=""):
    return {
        "columns": [
            {"name": "order_id", "type": "string", "meaning": "订单号", "risk": "无"},
            {"name": "order_date", "type": "date", "meaning": "下单日期", "risk": "跨季度边界需注意"},
            {"name": "region", "type": "string", "meaning": "销售大区", "risk": "取值含华东/华北/华南"},
            {"name": "channel", "type": "string", "meaning": "销售渠道", "risk": "无"},
            {"name": "amount", "type": "number", "meaning": "订单金额（含税）", "risk": "已取消订单也计入"},
            {"name": "coupon", "type": "number", "meaning": "优惠券抵扣额", "risk": "0 值表示未用券"},
            {"name": "status", "type": "string", "meaning": "订单状态", "risk": "含 cancelled，统计时须排除"},
            {"name": "refund_amount", "type": "number", "meaning": "退款金额（refunds 表）", "risk": "需按 order_id 关联"},
        ],
        "risks": [
            "营收类指标必须同时扣减 coupon 与 refunds，否则系统性高估",
            "orders 与 refunds 是一对多关系，直接 JOIN 后 SUM(amount) 会重复计数",
            "数据集不含成本字段，毛利率类指标无法计算",
        ],
        "notes": "订单表 1200 行，退款表 166 行，覆盖 2026-06-01 至 2026-10-14。",
    }


METRICS = ("营收", "订单量", "客单价", "退款率")


def _flags(deduct: bool) -> dict:
    """两轮的口径开关。首轮只做最直接的聚合，次轮按手册补齐。"""
    return {"exclude_cancelled": True,
            "coupon_deduction": deduct,
            "subtract_refund": deduct}


def planner(i, user=""):
    """首轮按"最直接的聚合"出计划（会漏掉券、退款，或按笔数算退款率），
    被驳回后第二轮补齐口径。这个两段式是演示剧情，与真实模型的行为一致：
    模型在没有人指出口径时，默认就是用最朴素的那个算法。"""
    p = parse(_q(user))
    metric = p["metric"] if p["metric"] in METRICS else "营收"
    wh = describe(p)
    f = _flags(i >= 1)
    filters = {"region": p["region"] or "", "start": p["start"], "end": p["end"], **f}

    if i == 0:
        return {
            "target_metric": metric,
            "steps": [
                {"step_id": 1, "desc": f"筛选 {wh} 的订单（状态非取消）", "expected": "订单明细"},
                {"step_id": 2, "desc": f"直接聚合得到{metric}", "expected": f"{metric}初值"},
            ],
            "filters": filters,
            "reasoning_note": "（首轮）按最直接的聚合口径计算。",
        }
    return {
        "target_metric": metric,
        "steps": [
            {"step_id": 1, "desc": f"筛选 {wh} 且状态非取消的订单", "expected": "订单明细"},
            {"step_id": 2, "desc": "按口径手册扣减优惠券与退款" if metric in ("营收", "客单价")
                                    else "严格排除已取消订单并按订单号去重", "expected": "口径修正后的明细"},
            {"step_id": 3, "desc": f"按{metric}的定义公式聚合", "expected": metric},
        ],
        "filters": filters,
        "reasoning_note": "（修订）按口径手册补齐扣减项与去重规则。",
    }


_HEAD = '''import csv, json
S, E, R = {start!r}, {end!r}, {regs!r}
rows = []
with open("orders.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if R and r["region"] not in R:
            continue
        if not (S <= r["order_date"] <= E):
            continue
        rows.append(r)
'''

_REVENUE = '''
total = 0.0
ids = set()
for r in rows:
    if {excl} and r["status"] == "cancelled":
        continue
    total += float(r["amount"]){coupon}
    ids.add(r["order_id"])
{refund_block}value = {value_expr}
method = "{method}"
'''

_COUNT = '''
if {honor_excl}:
    ids = {{r["order_id"] for r in rows if r["status"] != "cancelled"}}
    value = float(len(ids))
    method = "count(distinct order_id, exclude cancelled)"
else:
    value = float(len(rows))
    method = "count(rows, incl cancelled)"
'''

_AOV = '''
total = 0.0
ids = set()
for r in rows:
    if r["status"] == "cancelled":
        continue
    total += float(r["amount"]){coupon}
    ids.add(r["order_id"])
{refund_block}denom = float(len(ids)) if {honor_excl} else float(len(rows))
value = round((total - {refund_expr}) / denom, 2) if denom else 0.0
method = "revenue / order_count"
'''

_RATE = '''
gross = sum(float(r["amount"]) for r in rows if r["status"] != "cancelled")
ids = {{r["order_id"] for r in rows if r["status"] != "cancelled"}}
refund = 0.0
refund_cnt = 0
with open("refunds.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["order_id"] in ids:
            refund += float(r["refund_amount"])
            refund_cnt += 1
if {by_amount}:
    value = round(refund / gross * 100, 2) if gross else 0.0
    method = "sum(refund)/sum(amount)*100"
else:
    value = round(refund_cnt / len(ids) * 100, 2) if ids else 0.0
    method = "refund_count/order_count*100 (笔数口径，非金额口径)"
'''


def _code(p: dict, deduct: bool) -> str:
    """按指标与轮次生成代码。deduct=False 时用"朴素算法"，让审计有活可干。

    注意这里依然是**在沙箱里真跑**，不是直接把数字算好塞进去——
    所以沙箱、容错、超时这些机制在离线演示里同样被真实使用。

    四种指标的"首轮错误"各不相同，正好覆盖四类失败：
        营收   -> 漏扣券与退款（代码对、口径错，L3 才抓得住）
        订单量 -> COUNT 含取消单（口径写对了、代码写错，L2 抓）
        客单价 -> 分子分母口径不一致（L2 抓）
        退款率 -> 用笔数除笔数（L2 抓，正是手册里点明的陷阱）
    """
    regs = p["region"] or []
    if isinstance(regs, str):
        regs = [regs]
    metric = p["metric"] if p["metric"] in METRICS else "营收"
    f = _flags(deduct)

    if metric == "营收":
        body = _REVENUE.format(
            excl=f["exclude_cancelled"],
            coupon=' - float(r["coupon"])' if f["coupon_deduction"] else "",
            refund_block=('''
refund = 0.0
with open("refunds.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["order_id"] in ids:
            refund += float(r["refund_amount"])
''' if f["subtract_refund"] else "refund = 0.0\n"),
            value_expr="round(total - refund, 2)",
            method="sum(amount-coupon)-sum(refund)" if f["subtract_refund"] else "sum(amount)",
        )
        unit = "CNY"
    elif metric == "订单量":
        body = _COUNT.format(honor_excl=deduct and f["exclude_cancelled"])
        unit = "笔"
    elif metric == "客单价":
        body = _AOV.format(
            coupon=' - float(r["coupon"])' if f["coupon_deduction"] else "",
            refund_block=('''
refund = 0.0
with open("refunds.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["order_id"] in ids:
            refund += float(r["refund_amount"])
''' if f["subtract_refund"] else "refund = 0.0\n"),
            refund_expr="refund" if f["subtract_refund"] else "0.0",
            honor_excl=deduct and f["exclude_cancelled"],
        )
        unit = "CNY/笔"
    else:                                     # 退款率
        body = _RATE.format(by_amount=deduct)
        unit = "%"

    return (_HEAD.format(start=p["start"], end=p["end"], regs=list(regs))
            + body
            + f'print("RESULT:" + json.dumps({{"value": value, "unit": "{unit}", '
              f'"method": method}}, ensure_ascii=False))\n').strip()


def analyst(i, user=""):
    p = parse(_q(user))
    return {"code": _code(p, deduct=i >= 1),
            "plan_note": "首轮直接用最朴素算法" if i == 0 else "按修订计划补齐口径"}


def reporter(i, user=""):
    return {}


SCRIPTS = {
    "profiler": profiler,
    "planner": planner,
    "analyst": analyst,
    "reporter": reporter,
}
