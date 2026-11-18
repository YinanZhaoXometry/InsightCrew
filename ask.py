"""InsightCrew 操作入口：问一个问题，看系统怎么算、怎么被驳回、怎么改对。

三种用法
--------
    python ask.py                                # 交互式，提示你输入问题
    python ask.py "2026年8月华南区的营收是多少？"     # 直接问
    python ask.py "..." --compare                # 同时跑"有审计"和"无审计"，看差别

跑完会在 runs/<时间戳>/ 下留下三份东西：
    trace.jsonl      每一步的消息与事件（可作证据）
    blackboard.json  最终状态（含每版代码与数值）
    report.html      可视化时间线（自动用浏览器打开）

没有配置 IC_API_KEY 时自动进入离线模式：
流程、沙箱、审计全部真实运行，只有"写代码"这一步由规则生成，
因此不需要联网也能完整操作一遍。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import Config                          # noqa: E402
from core.blackboard import Critique               # noqa: E402
from core.orchestrator import Orchestrator         # noqa: E402

DEFAULT_Q = "2026 年第三季度华东区的营收是多少？"


def run_once(question: str, cfg: Config, no_critic: bool = False, verbose: bool = True):
    orch = Orchestrator(question, cfg=cfg, verbose=verbose)
    if no_critic:
        # 消融：让审计直接放行。用来回答"没有审计会怎样"。
        orch.critic.run = lambda artifact, _o=orch: Critique(verdict="PASS", issues=[])
    return orch, orch.run()


def money(v) -> str:
    return "—" if v is None else f"{v:,.2f}"


def explain(bb) -> None:
    """把一次运行讲成一句话，而不是让你去读 json。"""
    vals = [a.value for a in bb.artifacts]
    print("\n" + "─" * 70)
    print("这次运行发生了什么")
    print("─" * 70)
    for i, c in enumerate(bb.critiques, 1):
        v = vals[i - 1] if i - 1 < len(vals) else None
        state = {"PASS": "通过", "REVISE": "被驳回", "UNVERIFIED": "未验证"}.get(c.verdict, c.verdict)
        print(f"  第 {i} 轮  {money(v):>16}   {state}")
        for it in c.issues:
            print(f"          → [{it.get('layer')}] {it.get('issue')}")
    if len(vals) >= 2 and vals[0] and vals[-1]:
        d = vals[0] - vals[-1]
        pct = d / vals[-1] * 100 if vals[-1] else 0
        print(f"\n  差额：{money(d)}（第一轮高估 {pct:.1f}%）")
    print(f"  最终：{money(bb.latest_value)}　轮数：{bb.turn}　"
          f"降级：{'是' if bb.degraded else '否'}")
    print("─" * 70)


def _open_in_browser(html: str) -> None:
    """用真浏览器打开报告。

    Windows 上 .html 的默认程序很容易被编辑器抢走：右键"打开方式"里一旦选了
    VS Code，webbrowser.open() 就会把报告开在编辑器标签页里——看起来像"没生成"，
    其实是渲染不了内联 CSS 和 JS。所以先按安装路径找 Chrome / Edge，
    都找不到才退回系统默认程序。
    """
    url = "file:///" + html.replace("\\", "/")
    if os.name == "nt":
        candidates = [
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        ]
        for c in candidates:
            exe = os.path.expandvars(c)
            if os.path.exists(exe):
                try:
                    subprocess.Popen([exe, url])
                    return
                except OSError:
                    continue
    webbrowser.open(url)


def render(orch, open_browser: bool = True) -> str:
    subprocess.run([sys.executable, "-m", "ui.render_report", orch.run_dir],
                   cwd=os.path.dirname(os.path.abspath(__file__)),
                   check=False, stdout=subprocess.DEVNULL)
    html = os.path.join(orch.run_dir, "report.html")
    if open_browser and os.path.exists(html):
        _open_in_browser(html)
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*", help="用自然语言问一个问题")
    ap.add_argument("--compare", action="store_true", help="对比有审计 / 无审计两种配置")
    ap.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    ap.add_argument("--quiet", action="store_true", help="只看结论，不打过程")
    args = ap.parse_args()

    q = " ".join(args.question).strip()
    if not q:
        print("输入你的问题（直接回车用示例）：", end=" ")
        q = input().strip() or DEFAULT_Q

    cfg = Config()
    mode = "离线演示" if cfg.offline else f"{cfg.model}"
    print(f"\n问题：{q}")
    print(f"模式：{mode}　｜　审计：开启（加 --compare 可对照关闭时的结果）\n")

    if args.compare:
        # 先跑无审计，再跑有审计——顺序固定，输出才好对照
        _, bb_no = run_once(q, cfg, no_critic=True, verbose=not args.quiet)
        _, bb_yes = run_once(q, cfg, no_critic=False, verbose=False)
        print("\n" + "═" * 70)
        print("对照结果")
        print("═" * 70)
        print(f"  无审计　{money(bb_no.latest_value):>16}　{bb_no.turn} 轮")
        print(f"  有审计　{money(bb_yes.latest_value):>16}　{bb_yes.turn} 轮")
        if bb_no.latest_value and bb_yes.latest_value and bb_no.latest_value != bb_yes.latest_value:
            d = bb_no.latest_value - bb_yes.latest_value
            print(f"\n  两个答案相差 {money(d)}（{d / bb_yes.latest_value * 100:.1f}%）")
            print("  无审计时系统只跑一轮就交付了，它没有任何机制发现自己算错。")
        print("═" * 70)
        return

    orch, bb = run_once(q, cfg, verbose=not args.quiet)
    print("\n" + "═" * 70)
    print(bb.report)
    print("═" * 70)
    explain(bb)
    html = render(orch, open_browser=not args.no_open)
    print(f"\n运行记录：{orch.run_dir}")
    if html and os.path.exists(html):
        print(f"可视化回放：{html}")


if __name__ == "__main__":
    main()
