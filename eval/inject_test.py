"""错误注入测试：审计器的检出率（不需要调用任何模型）。

为什么要有这个测试（答辩时的说法）：
    "模型准确率"受模型本身能力影响，受网络影响，还受温度影响，换台机器跑就不一样。
    但"审计器能不能抓住一个已知的错误答案"是**确定的**：输入固定、判据固定、输出可复现。
    这正是本项目最该被验证的东西——因为我们的卖点不是"模型更聪明"，而是"错误拦得住"。

五种注入（每道题都跑一遍）：
    OK        完全正确的答案          → 期望 PASS（若被驳回，就是误报，会拖慢流程）
    NUMERIC   filters 对、数字算错      → 期望 REVISE，且必须由 L2 抓到
    NO_REFUND 漏扣退款                → 期望 REVISE，且必须由 L3 抓到（L2 会"同意"这个错答案）
    NO_COUPON 漏扣优惠券              → 期望 REVISE，且必须由 L3 抓到
    NO_CANCEL 未排除取消订单           → 期望 REVISE，且必须由 L3 抓到

关键的一行在 NO_REFUND 那组：注入的数值是**按错误口径真实算出来的**，
所以 L2 双路径复算会完全一致（SQL 也按同一套 filters 算）。此时只有 L3 能拦。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.critic import Critic                    # noqa: E402
from core.blackboard import Blackboard              # noqa: E402
from core.messages import MessageBus                # noqa: E402
from tools.data_tools import l2_value               # noqa: E402
from tools.metric_dict import MetricDict            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

CASES = [
    # 名称,                filters 改动,            数值怎么造,           期望判定, 期望层
    ("OK",                 {},                      "正确值",             "PASS",   None),
    ("NUMERIC",            {},                      "放大 13%",           "REVISE", "L2"),
    ("NO_REFUND",          {"subtract_refund": False},    "按错口径算",    "REVISE", "L3"),
    ("NO_COUPON",          {"coupon_deduction": False},   "按错口径算",    "REVISE", "L3"),
    ("NO_CANCEL",          {"exclude_cancelled": False},  "按错口径算",    "REVISE", "L3"),
]


def judge(question: str, filters: dict, value: float) -> tuple[str, str]:
    """让真实的 Critic 判一次，返回 (verdict, 层)。"""
    tmp = tempfile.mkdtemp(prefix="inject-")
    bus = MessageBus(tmp, verbose=False)
    bb = Blackboard(question=question)
    bb.new_plan([{"step_id": 1, "desc": "注入测试", "expected": "数值"}], filters, "营收")
    bb.add_artifact(step_id=1, code="# injected", run_result={"ok": True},
                    value=value, unit="元", method="injected")
    c = Critic(llm=None, bus=bus, bb=bb, metric_dict=MetricDict()).run(bb.artifacts[-1])
    bus.close()
    return c.verdict, c.layer


def main():
    rows = [json.loads(l) for l in open(os.path.join(HERE, "questions.jsonl"), encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("expected") is not None]

    stats = {name: {"hit": 0, "n": 0, "layer_ok": 0} for name, *_ in CASES}
    detail = []

    print(f"{'题号':<5}{'注入':<11}{'数值':>14}{'判定':<10}{'层':<8}结果")
    print("-" * 66)
    for r in rows:
        base = dict(r["filters"])
        for name, patch, how, want_v, want_layer in CASES:
            f = {**base, **patch}
            if how == "正确值":
                val = r["expected"]
            elif how == "放大 13%":
                val = round(r["expected"] * 1.13, 2)
            else:
                val = round(l2_value(f), 2)          # 按错误口径真实算出来的值

            verdict, layer = judge(r["question"], f, val)
            hit = verdict == want_v
            layer_ok = (want_layer is None) or (want_layer in layer)
            s = stats[name]
            s["n"] += 1
            s["hit"] += hit
            s["layer_ok"] += layer_ok
            mark = "✓" if (hit and layer_ok) else "✗"
            print(f"{r['id']:<5}{name:<11}{val:>14,.2f}{verdict:<10}{layer:<8}{mark}")
            detail.append({"id": r["id"], "case": name, "value": val, "verdict": verdict,
                           "layer": layer, "expect": want_v, "want_layer": want_layer,
                           "hit": hit, "layer_ok": layer_ok})

    print("\n" + "=" * 66)
    print(f"{'注入类型':<12}{'应拒/应过':<12}{'正确判定':<10}{'层级正确':<10}说明")
    for name, *_ in CASES:
        s = stats[name]
        role = "应 PASS" if name == "OK" else "应 REVISE"
        note = "误报率" if name == "OK" else "检出率"
        print(f"{name:<12}{role:<12}{s['hit']}/{s['n']:<8}{s['layer_ok']}/{s['n']:<8}{note}")

    ok_fp = stats["OK"]["n"] - stats["OK"]["hit"]
    caught = sum(stats[k]["hit"] for k in stats if k != "OK")
    total = sum(stats[k]["n"] for k in stats if k != "OK")
    print(f"\n错误拦截率 {caught}/{total} = {caught / total:.1%}　｜　误报 {ok_fp}/{stats['OK']['n']}")
    l3_only = sum(1 for d in detail if d["case"].startswith("NO_") and d["layer_ok"])
    print(f"仅靠 L3 才拦住的错误：{l3_only} 例（这些例子里 L2 复算与错误答案完全一致）")

    out = os.path.join(HERE, "result_inject.json")
    json.dump({"stats": stats, "detail": detail}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
