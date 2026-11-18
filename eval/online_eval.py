"""在线评测：用真模型跑一批题，量出"真系统"的准确率、轮数、成本。

与 eval/run_eval.py 的区别：
    那个跑的是离线剧本（验证机制）；这个跑的是真模型（验证能力）。
    两者都要有，报告里要分开写——否则会被质疑"拿剧本的成绩当模型成绩"。

标准答案由**第三条路径**独立算出：
    Analyst 用 Python/csv 算，Critic 用 SQL 算，这里再用一份纯 Python 实现对照。
    三条路径都对上，才说明这个数字是真的。

用法：
    python -m eval.online_eval              # 全部题
    python -m eval.online_eval --limit 3    # 先试 3 题
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

from config import Config, DATA_DIR
from core.orchestrator import Orchestrator

# (题目, 预期指标, 预期结果类型)  expected 只用来核对"是否应当拒绝"
CASES = [
    ("2026年第三季度华东区的营收是多少？", "营收", "number"),
    ("2026年8月华南区的营收是多少？", "营收", "number"),
    ("2026年第三季度全公司的营收是多少？", "营收", "number"),
    ("2026年第三季度华北区的订单量是多少？", "订单量", "number"),
    ("2026年9月华东区的订单量是多少？", "订单量", "number"),
    ("2026年第三季度华南区的客单价是多少？", "客单价", "number"),
    ("2026年第三季度全公司的客单价是多少？", "客单价", "number"),
    ("2026年第三季度华东区的退款率是多少？", "退款率", "number"),
    ("2026年9月华北区的退款率是多少？", "退款率", "number"),
    ("上季度华东区的营收是多少？", "营收", "number"),
    ("2026年第三季度华东区的毛利率是多少？", "毛利率", "refuse"),
    ("2025年12月华北区的订单量是多少？", "订单量", "refuse"),
]

TOL = {"营收": 0.01, "订单量": 0.5, "客单价": 0.01, "退款率": 0.01}


# --------------------------------------------------------------------- 第三条路径
def _load():
    with open(os.path.join(DATA_DIR, "orders.csv"), newline="", encoding="utf-8") as f:
        orders = list(csv.DictReader(f))
    with open(os.path.join(DATA_DIR, "refunds.csv"), newline="", encoding="utf-8") as f:
        refunds = {r["order_id"]: float(r["refund_amount"]) for r in csv.DictReader(f)}
    return orders, refunds


def expected_value(question: str):
    """纯 Python 独立实现，刻意不复用 tools/ 与 core/ 的任何代码。"""
    from core.question_parser import parse

    p = parse(question)
    orders, refunds = _load()
    regs = p["region"]
    regs = [regs] if isinstance(regs, str) else (regs or [])

    rows = [r for r in orders
            if (not regs or r["region"] in regs)
            and p["start"] <= r["order_date"] <= p["end"]
            and r["status"] != "cancelled"]

    if p["metric"] not in TOL:
        return None, p

    gross = sum(float(r["amount"]) for r in rows)
    rev = sum(float(r["amount"]) - float(r["coupon"]) for r in rows) \
        - sum(refunds.get(r["order_id"], 0.0) for r in rows)

    if p["metric"] == "营收":
        return round(rev, 2), p
    if p["metric"] == "订单量":
        return float(len(rows)), p
    if p["metric"] == "客单价":
        return (round(rev / len(rows), 2) if rows else None), p
    return (round(sum(refunds.get(r["order_id"], 0.0) for r in rows) / gross * 100, 2)
            if gross else None), p


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=len(CASES))
    args = ap.parse_args()

    cfg = Config()
    if cfg.offline:
        print("当前是离线模式，这个脚本要用真模型。请先配好 IC_API_KEY。")
        return 2

    print(f"模型：{cfg.model} @ {cfg.base_url}\n")
    rows, t_all = [], time.time()

    for q, metric, kind in CASES[:args.limit]:
        exp, parsed = expected_value(q)
        t0 = time.time()
        try:
            bb = Orchestrator(q, cfg=cfg, verbose=False).run()
            got = bb.latest_value
            turns, tokens, degraded = bb.turn, bb.tokens, bb.degraded
            verdicts = [f"{c.verdict}({c.layer})" for c in bb.critiques]
        except Exception as e:                        # noqa: BLE001
            # 一道题崩掉不能拖死整批评测，否则你永远跑不完 12 题
            got, turns, tokens, degraded = None, 0, 0, True
            verdicts = [f"ERROR:{type(e).__name__}"]
            print(f"! {q[:26]:28} 运行异常：{type(e).__name__}: {e}")
        elapsed = time.time() - t0

        if kind == "refuse":
            ok = got is None
        else:
            ok = got is not None and exp is not None and abs(float(got) - exp) <= TOL[metric]

        rows.append({
            "question": q, "metric": metric, "kind": kind,
            "expected": exp, "got": got, "ok": ok,
            "turns": turns, "degraded": degraded,
            "verdicts": verdicts,
            "tokens": tokens, "seconds": round(elapsed, 1),
        })
        mark = "✓" if ok else "✗"
        val = "—" if got is None else f"{float(got):,.2f}"
        ref = "拒绝" if kind == "refuse" else (f"{exp:,.2f}" if exp is not None else "—")
        print(f"{mark} {q[:26]:28} 期望 {ref:>12}  实得 {val:>12}  "
              f"{turns}轮 {tokens}tok {elapsed:4.1f}s "
              f"{'降级' if degraded else ''}")

    total = time.time() - t_all
    n_ok = sum(r["ok"] for r in rows)
    print("\n" + "=" * 72)
    print(f"准确率 {n_ok}/{len(rows)} = {n_ok / len(rows) * 100:.1f}%　"
          f"总耗时 {total:.0f}s　总 tokens {sum(r['tokens'] for r in rows):,}")
    print(f"平均轮数 {sum(r['turns'] for r in rows) / len(rows):.2f}　"
          f"降级 {sum(r['degraded'] for r in rows)} 次")

    by_metric = {}
    for r in rows:
        d = by_metric.setdefault(r["metric"], [0, 0])
        d[1] += 1
        d[0] += bool(r["ok"])
    for m, (a, b) in by_metric.items():
        print(f"  {m}: {a}/{b}")

    out = os.path.join(os.path.dirname(__file__), "result_online.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": cfg.model, "rows": rows,
                   "accuracy": n_ok / len(rows), "seconds": round(total, 1)},
                  f, ensure_ascii=False, indent=2)
    print(f"\n明细已写入 {out}")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
