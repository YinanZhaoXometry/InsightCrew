"""把一次运行渲染成单文件 HTML 时间线（零依赖，双击即可看）。

为什么不用 Streamlit：
    演示视频只需要"看得清的执行过程"，不需要交互。单文件 HTML 更可靠：
    不装包、不起服务、不受网络影响，还能直接发给组员和助教看。

用法：
    python -m ui.render_report runs/20261105-162120
    python -m ui.render_report            # 渲染最新一次
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

from config import RUNS_DIR

AGENT_STYLE = {
    "Profiler": ("#E6F1FB", "#0C447C"),
    "Planner": ("#EEEDFE", "#3C3489"),
    "Analyst": ("#E1F5EE", "#085041"),
    "Critic": ("#FAEEDA", "#633806"),
    "Reporter": ("#FBEAF0", "#72243E"),
    "User": ("#F1EFE8", "#2C2C2A"),
}
VERDICT_STYLE = {
    "REVISE": ("#FCEBEB", "#A32D2D", "驳回"),
    "PASS": ("#E1F5EE", "#0F6E56", "通过"),
    "UNVERIFIED": ("#FAEEDA", "#854F0B", "未验证"),
}


def _md_to_html(md: str) -> str:
    """极简 Markdown 渲染：够用即可，避免引入依赖。"""
    out, in_list, in_code = [], False, False
    for raw in md.splitlines():
        line = html.escape(raw)
        if line.startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(line)
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line.strip():
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        if line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        else:
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


def _badge(name: str) -> str:
    bg, fg = AGENT_STYLE.get(name, ("#F1EFE8", "#2C2C2A"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{html.escape(name)}</span>'


def _detail(ev: dict) -> str:
    if ev["kind"] == "message":
        parts = []
        payload = ev.get("payload") or {}
        if "filters" in payload:
            parts.append("<div class='kv'><b>filters</b> "
                         + html.escape(json.dumps(payload["filters"], ensure_ascii=False)) + "</div>")
        if "steps" in payload:
            steps = "".join(f"<li>{html.escape(str(s.get('desc', s)))}</li>" for s in payload["steps"])
            parts.append(f"<ul class='steps'>{steps}</ul>")
        if "issues" in payload and payload["issues"]:
            items = "".join(
                f"<li><b>{html.escape(i.get('layer',''))}</b> {html.escape(i.get('issue',''))}"
                f"<span class='src'>{html.escape(i.get('detail',''))}</span></li>"
                for i in payload["issues"])
            parts.append(f"<ul class='issues'>{items}</ul>")
        if "value" in payload:
            parts.append(f"<div class='kv'><b>value</b> {payload['value']:,.2f}</div>")
        return "".join(parts)
    return ""


def render(run_dir: str) -> str:
    trace_path = os.path.join(run_dir, "trace.jsonl")
    bb_path = os.path.join(run_dir, "blackboard.json")
    with open(trace_path, encoding="utf-8") as f:
        events = [json.loads(l) for l in f if l.strip()]
    bb = {}
    if os.path.exists(bb_path):
        with open(bb_path, encoding="utf-8") as f:
            bb = json.load(f)

    rows = []
    for ev in events:
        kind = ev.get("kind")
        if kind == "message":
            extra = _detail(ev)
            rows.append(f"""<div class="row">
  <div class="t">{ev.get('t','')}</div>
  <div class="c">{_badge(ev['sender'])}<span class="arrow">→</span>{_badge(ev['to'])}
    <span class="intent">{html.escape(ev.get('intent',''))}</span>
    <div class="note">{html.escape(ev.get('note',''))}</div>{extra}</div>
</div>""")
        elif kind == "tool":
            rows.append(f"""<div class="row"><div class="t">{ev.get('t','')}</div>
  <div class="c tool">{html.escape(ev.get('summary',''))}</div></div>""")
        elif kind == "verdict":
            summary = ev.get("summary", "")
            v = summary.split(" ")[0]
            tail = summary[len(v):].lstrip(" |")
            bg, fg, label = VERDICT_STYLE.get(v, ("#F1EFE8", "#2C2C2A", v))
            rows.append(f"""<div class="row"><div class="t">{ev.get('t','')}</div>
  <div class="c"><span class="badge" style="background:{bg};color:{fg}">{label}</span>
  <span class="vtext" style="color:{fg}">{html.escape(tail)}</span></div></div>""")
        else:
            rows.append(f"""<div class="row"><div class="t">{ev.get('t','')}</div>
  <div class="c muted">{html.escape(ev.get('summary',''))}</div></div>""")

    arts = []
    for a in bb.get("artifacts", []):
        val = "-" if a.get("value") is None else f"{a['value']:,.2f}"
        arts.append(f"""<details class="art"><summary><b>{a['aid']}</b>
 　值 {val} 　口径 <code>{html.escape(a.get('method') or '-')}</code>
 　{'执行成功' if a.get('ok') else '执行失败'}</summary>
 <pre>{html.escape(a.get('code',''))}</pre></details>""")

    last_val = next((a["value"] for a in reversed(bb.get("artifacts", []))
                     if a.get("value") is not None), None)

    crit_chain = "".join(
        f"<li>{i}. <b>{c['verdict']}</b> — "
        + html.escape("；".join(x["issue"] for x in c.get("issues", [])) or "全部通过")
        + "</li>" for i, c in enumerate(bb.get("critiques", []), 1))

    title = "InsightCrew 运行回放"
    body = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>
:root{{--bd:rgba(0,0,0,.08);--mut:#5f5e5a}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f6f6f3;color:#2c2c2a;
 font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:960px;margin:0 auto;padding:36px 20px 80px}}
h1{{font-size:21px;font-weight:600;margin:0 0 6px}}
h2{{font-size:16px;font-weight:600;margin:26px 0 10px}}
h3{{font-size:14px;font-weight:600;margin:18px 0 8px}}
.sub{{color:var(--mut);font-size:12.5px;margin-bottom:6px}}
.card{{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:18px 20px}}
.stats{{display:flex;gap:26px;flex-wrap:wrap;margin:14px 0 4px}}
.stat b{{display:block;font-size:19px;font-weight:600}}
.stat span{{color:var(--mut);font-size:12px}}
.row{{display:flex;gap:14px;padding:9px 0;border-bottom:1px dashed rgba(0,0,0,.06)}}
.row:last-child{{border-bottom:0}}
.t{{width:56px;flex:none;color:#9a9890;font-size:11.5px;padding-top:3px;
 font-variant-numeric:tabular-nums}}
.c{{flex:1;min-width:0}}
.badge{{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:500}}
.arrow{{color:#b4b2a9;margin:0 7px;font-size:12px}}
.intent{{margin-left:9px;font-size:11px;color:#888780;letter-spacing:.03em}}
.note{{margin-top:3px}}
.tool{{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:#444441;padding-left:2px}}
.muted{{color:#888780;font-size:12.5px}}
.kv{{font-size:12px;color:#5f5e5a;margin-top:4px}}
.kv b{{color:#2c2c2a;margin-right:6px}}
.steps{{margin:4px 0 0 18px;padding:0;font-size:12.5px;color:#5f5e5a}}
.issues{{margin:6px 0 0 0;padding-left:18px;color:#a32d2d;font-size:12.5px}}
.src{{color:#9a9890;margin-left:6px}}
.vtext{{margin-left:8px;font-size:12.5px}}
details.art{{border:1px solid var(--bd);border-radius:8px;padding:8px 12px;margin:7px 0;background:#fcfcfa}}
details.art summary{{cursor:pointer;font-size:12.5px}}
pre{{background:#f6f6f3;border:1px solid var(--bd);border-radius:8px;padding:12px;
 overflow:auto;font-size:12px;line-height:1.6;margin:10px 0 2px}}
code{{background:#f1efe8;padding:1px 5px;border-radius:4px;font-size:12px}}
ul{{margin:6px 0 6px 18px;padding:0}}
.report{{margin-top:8px}}
.report h1,.report h2{{border:0}}
footer{{margin-top:30px;color:#9a9890;font-size:11.5px}}
</style></head><body><div class="wrap">
<h1>{html.escape(bb.get('question',''))}</h1>
<div class="sub">{html.escape(os.path.basename(os.path.normpath(run_dir)))} ·
 {len(events)} 条事件 · 由 Orchestrator 生成</div>

<div class="card">
<div class="stats">
 <div class="stat"><b>{bb.get('turn','-')}</b><span>轮数</span></div>
 <div class="stat"><b>{len(bb.get('artifacts',[]))}</b><span>执行版本</span></div>
 <div class="stat"><b>{html.escape(str(bb.get('target_metric','-')))}</b><span>目标指标</span></div>
 <div class="stat"><b>{'是' if bb.get('degraded') else '否'}</b><span>降级输出</span></div>
 <div class="stat"><b>{html.escape(f"{last_val:,.2f}" if last_val is not None else "-")}</b><span>最终值</span></div>
</div>
<h2 style="margin-top:18px">执行时间线</h2>
{''.join(rows)}
</div>

<h2>各版本执行代码</h2>
{''.join(arts) or '<div class="sub">无</div>'}

<h2>审计链</h2>
<ul>{crit_chain}</ul>

<h2>最终报告</h2>
<div class="card report">{_md_to_html(bb.get('report',''))}</div>

<footer>InsightCrew · 多智能体数据分析 + 独立审计 · 生成于 {__import__('time').strftime('%Y-%m-%d %H:%M')}</footer>
</div></body></html>"""

    out = os.path.join(run_dir, "report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    return out


def main():
    if len(sys.argv) > 1:
        run_dir = sys.argv[1]
    else:
        dirs = sorted(d for d in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, d)))
        if not dirs:
            print("runs/ 下还没有运行记录，先执行 python run_demo.py")
            return
        run_dir = os.path.join(RUNS_DIR, dirs[-1])
    print(render(run_dir))


if __name__ == "__main__":
    main()
