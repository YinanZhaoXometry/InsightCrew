"""命令行入口：python run_demo.py ["你的问题"]

跑完会在 runs/<时间戳>/ 下生成：
    trace.jsonl      全链路消息与事件（可回放、可举证）
    blackboard.json  最终状态（含每个版本的代码与数值）
    report.html      可视化时间线（用浏览器打开，录视频用这个）

没有 IC_API_KEY 时自动进入离线剧本模式，流程完全一致。
"""
from __future__ import annotations

import sys

from core.orchestrator import Orchestrator

DEFAULT_Q = "2026 年第三季度华东区的营收是多少？"


def main():
    q = " ".join(sys.argv[1:]).strip() or DEFAULT_Q
    orch = Orchestrator(q)
    bb = orch.run()

    print("\n" + "═" * 74)
    print(bb.report)
    print("═" * 74)
    print(f"\n运行目录：{orch.run_dir}")
    print(f"轮数：{bb.turn}｜执行次数：{len(bb.artifacts)}｜"
          f"降级：{'是' if bb.degraded else '否'}")
    print(f"复盘命令：python -m ui.render_report {orch.run_dir}")


if __name__ == "__main__":
    main()
