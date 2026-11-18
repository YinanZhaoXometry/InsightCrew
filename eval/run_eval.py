"""评估脚本：跑完整个评估集，输出准确率表格。

三组消融的做法（对应分析分 15%）：
    python -m eval.run_eval --mode full        # 完整系统（默认）
    python -m eval.run_eval --mode no_critic   # 去掉审计
    python -m eval.run_eval --mode single      # 单 agent 基线

注意：离线剧本模式只对 Q01 有效（剧本是钉死的），
跑完整评估集必须配置 IC_API_KEY。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config                      # noqa: E402
from core.blackboard import Critique           # noqa: E402
from core.orchestrator import Orchestrator     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 1.0   # 与标准答案误差在 1 元以内算对


def load_questions():
    with open(os.path.join(HERE, "questions.jsonl"), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=["full", "no_critic", "single"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = Config()
    if cfg.offline:
        print("⚠ 当前为离线剧本模式，只有 Q01 有预置答案；完整评估请设置 IC_API_KEY。\n")

    rows = load_questions()
    if args.limit:
        rows = rows[: args.limit]

    results, t0 = [], time.time()
    for q in rows:
        orch = Orchestrator(q["question"], cfg=cfg, verbose=False)

        # ---- 消融：去掉审计（用于证明收益来自"审计"而非"多调几次模型"）----
        if args.mode == "no_critic":
            orch.critic.run = lambda artifact, _o=orch: Critique(verdict="PASS", issues=[])

        # ---- 消融：单 agent 基线（无画像、无计划、无口径注入）----
        if args.mode == "single":
            orch.profiler.run = lambda _o=orch: {}
            def _flat_plan(critique=None, _o=orch):
                _o.bb.new_plan([{"step_id": 1, "desc": "直接回答用户问题", "expected": "数值"}],
                               {}, "营收")
            orch.planner.run = _flat_plan
            orch.critic.run = lambda artifact, _o=orch: Critique(verdict="PASS", issues=[])
        bb = orch.run()
        got = bb.latest_value
        exp = q["expected"]
        if exp is None:
            ok = got is None
        else:
            ok = got is not None and abs(got - exp) <= TOL
        results.append({"id": q["id"], "cat": q["category"], "expected": exp,
                        "got": got, "ok": ok, "turns": bb.turn,
                        "degraded": bb.degraded, "tokens": bb.tokens})
        mark = "✓" if ok else "✗"
        print(f"{mark} {q['id']} [{q['category']}] 期望 {exp if exp is None else f'{exp:,.2f}'} "
              f"实得 {'—' if got is None else f'{got:,.2f}'} 轮数 {bb.turn}")

    n = len(results)
    acc = sum(r["ok"] for r in results) / n if n else 0
    by_cat = {}
    for r in results:
        by_cat.setdefault(r["cat"], []).append(r["ok"])
    print("\n" + "=" * 60)
    print(f"总准确率 {acc:.1%}（{sum(r['ok'] for r in results)}/{n}）  模式={args.mode}")
    for cat, oks in by_cat.items():
        print(f"  {cat:<8} {sum(oks)}/{len(oks)}")
    print(f"平均轮数 {sum(r['turns'] for r in results) / n:.2f}｜"
          f"总耗时 {time.time() - t0:.1f}s｜降级 {sum(r['degraded'] for r in results)} 次")

    out = os.path.join(HERE, f"result_{args.mode}_{time.strftime('%m%d-%H%M')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"mode": args.mode, "acc": acc, "results": results}, f,
                  ensure_ascii=False, indent=2)
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
