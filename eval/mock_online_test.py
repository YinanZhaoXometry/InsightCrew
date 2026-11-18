"""在线模式的"假模型"端到端测试。

为什么需要它：
    接真模型之前，我们无法证明 `core/llm.py` 的在线分支是通的——
    离线剧本走的是另一条代码路径，测不到 JSON 解析、HTTP 重试、prompt 契约。
    这里起一个本地 OpenAI 兼容服务，返回"模型本应给出的那种回答"，
    于是整条在线链路（HTTP → JSON → 沙箱 → 审计 → 报告）都被真实执行。

它测不出"模型够不够聪明"，但能测出"接口接得对不对"——
后者恰恰是接真模型时最先炸的地方。

用法：python -m eval.mock_online_test
"""
from __future__ import annotations

import ast
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import Config
from core.orchestrator import Orchestrator

QUESTION = "2026年第三季度华东区的营收是多少？"
EXPECT = 1036538.28

PLANNER_OUT = {
    "target_metric": "营收",
    "steps": [
        {"step_id": 1, "desc": "筛选 2026Q3 华东区非取消订单", "expected": "订单明细"},
        {"step_id": 2, "desc": "金额减退优惠券，再减退款", "expected": "净营收"},
    ],
    "filters": {"region": "华东", "start": "2026-07-01", "end": "2026-09-30",
                "exclude_cancelled": True, "coupon_deduction": True,
                "subtract_refund": True},
}

PROFILER_OUT = {
    "columns": [{"name": "amount", "type": "float", "meaning": "订单金额",
                 "risk": "未扣优惠券与退款"}],
    "risks": ["refunds 表需按 order_id 关联", "取消订单不应计入"],
    "notes": "2026-06 ~ 2026-10",
}

CODE_TMPL = '''
import csv, json
S, E = {start!r}, {end!r}
regs = {regs!r}
ids, total = set(), 0.0
with open("orders.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if regs and r["region"] not in regs:
            continue
        if not (S <= r["order_date"] <= E):
            continue
        if r["status"] == "cancelled":
            continue
        total += float(r["amount"]) - {coupon}
        ids.add(r["order_id"])
refund = 0.0
with open("refunds.csv", newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["order_id"] in ids:
            refund += float(r["refund_amount"])
value = round(total - {refund_expr}, 2)
print("RESULT:" + json.dumps({{"value": value, "unit": "CNY", "method": "mock:sum(amount-coupon)-sum(refund)"}}))
'''


def _analyst_out(user: str) -> dict:
    """按 prompt 里给出的口径参数生成代码——顺带验证 prompt 是否真的带够了信息。"""
    m = re.search(r"口径参数：(\{.*?\})", user, re.S)
    f = ast.literal_eval(m.group(1)) if m else {}
    reg = f.get("region")
    regs = list(reg) if isinstance(reg, (list, tuple)) else ([reg] if reg else [])
    code = CODE_TMPL.format(
        start=f.get("start"), end=f.get("end"), regs=regs,
        coupon='float(r["coupon"])' if f.get("coupon_deduction") else "0.0",
        refund_expr="refund" if f.get("subtract_refund") else "0.0",
    )
    return {"code": "```python" + code + "```",   # 故意加围栏，验证剥壳逻辑
            "plan_note": "按口径扣减券与退款"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):                              # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "Planner" in system:
            out = PLANNER_OUT
        elif "Analyst" in system:
            out = _analyst_out(user)
        elif "Profiler" in system:
            out = PROFILER_OUT
        else:
            out = {"ok": True, "echo": "pong"}
        payload = {
            "choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}],
            "usage": {"total_tokens": 100},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):                      # 静音
        pass


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"假模型服务已启动：http://127.0.0.1:{port}/v1")

    cfg = Config(api_key="test-key", base_url=f"http://127.0.0.1:{port}/v1",
                 model="mock-model", max_seconds=60)
    print(f"运行模式：{'离线' if cfg.offline else '在线（HTTP 真实往返）'}")

    orch = Orchestrator(QUESTION, cfg=cfg, verbose=False)
    bb = orch.run()
    srv.shutdown()

    v = bb.latest_value
    ok_val = v is not None and abs(float(v) - EXPECT) <= 0.01
    ok_pass = bool(bb.critiques) and bb.critiques[-1].verdict == "PASS"
    print()
    print(f"问题     ：{QUESTION}")
    print(f"结论     ：{v:,.2f}" if v is not None else "结论     ：无数字")
    print(f"期望     ：{EXPECT:,.2f}")
    print(f"判定链   ：{['%s(%s)' % (c.verdict, c.layer) for c in bb.critiques]}")
    print(f"轮数/耗时：{bb.turn} / {bb.notes[-1] if bb.notes else '-'}")
    print(f"降级     ：{bb.degraded}")
    print()
    print(f"数值一致 ：{'✓' if ok_val else '✗'}")
    print(f"审计通过 ：{'✓' if ok_pass else '✗'}")
    print(f"在线链路 ：✓ HTTP → JSON → 沙箱 → 审计 → 报告 全程真实执行"
          if ok_val and ok_pass else "在线链路 ：✗ 有问题，看上面的回放目录")
    print(f"回放目录 ：runs/{orch.run_id}")
    return 0 if (ok_val and ok_pass) else 1


if __name__ == "__main__":
    raise SystemExit(main())
