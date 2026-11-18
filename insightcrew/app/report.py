"""报告：把黑板快照渲染成中文 Markdown。它只读、不计算。

报告里的每一个数字都来自黑板上的事实或已通过审计的方案；数值只做展示格式化，不做任何运算。
第二部分（结论）在 REVIEW 时绝不出现评级与价位；评审意见一节属于观点，不参与核验。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..kernel.events import Event

_REVIEW_REASON = {
    "invalid_ticker": "代码格式不正确",
    "unknown_ticker": "代码不在数据集里",
    "no_data": "该日期之前没有已收盘的行情",
    "future_date": "分析日期晚于今天",
    "stale_data": "数据过旧",
    "insufficient_history": "历史数据不足",
    "source_unavailable": "行情数据源不可用",
    "ledger_inconsistent": "两条计算路径的结果不一致",
    "malformed_output": "模型输出两次都不合契约",
    "stagnation": "同样的问题连续两次未被修正",
    "audit_failed": "多次打回后审计仍未通过",
    "contested": "评审多数反对，未达成共识",
    "budget_exhausted": "token 预算已用尽",
    "time_exhausted": "时间预算已用尽",
    "calls_exhausted": "模型请求次数已用尽",
    "model_unavailable": "模型不可用",
}
_JUDGE_NAME = {"evidence": "证据评审", "conservative": "保守评审", "aggressive": "进取评审"}
_STAGE_NAME = {"technical": "技术分析", "debate": "多空辩论", "manager": "研究主管", "trader": "交易员"}
_MODE_NAME = {"model": "真实模型", "scripted": "脚本化模型（回复为预先编写）", "replay": "回放"}
_DASH = "—"


def render(snapshot: dict[str, Any], events: Sequence[Event] | None = None, run_id: str = "") -> str:
    """把一次运行的黑板快照渲染成报告。events 与 run_id 供第 9、11、12 节使用；缺省时相应小节从简。"""
    started = _run_started(events)
    parts = [_title(snapshot, started), _conclusion(snapshot), _confidence(snapshot),
             _facts(snapshot), _thesis(snapshot), _opinions(snapshot), _rules(snapshot),
             _judges(snapshot), _rework(events), _quality(snapshot), _usage(snapshot, started),
             _run_info(snapshot, started, run_id)]
    return "\n\n".join(parts) + "\n"


def _reason_text(reason: str) -> str:
    return _REVIEW_REASON.get(reason, reason)


def _num(value: Any) -> str:
    """数值的展示格式：最多四位小数，去掉多余的零。只做格式化，不做运算。"""
    if value is None:
        return _DASH
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


def _run_started(events: Sequence[Event] | None) -> dict[str, Any]:
    for event in events or ():
        if event.type == "run.started":
            return event.data
    return {}


# ---- 第 1 节：标题 ----

def _title(snapshot: dict[str, Any], started: dict[str, Any]) -> str:
    scope = snapshot.get("request/scope", {})
    instrument = snapshot.get("ledger/instrument", {})
    ticker = instrument.get("ticker") or scope.get("ticker") or started.get("request", {}).get("ticker") or _DASH
    name = instrument.get("name") or ""
    as_of = scope.get("as_of") or started.get("request", {}).get("as_of") or _DASH
    head = f"# {ticker} {name}分析报告（分析日 {as_of}）".replace("  ", " ")
    return head + "\n\n本报告由课程项目自动生成，不构成投资建议；系统不执行、不路由、也不模拟任何订单。"


# ---- 第 2 节：结论 ----

def _conclusion(snapshot: dict[str, Any]) -> str:
    outcome = snapshot.get("run/outcome", {})
    lines = ["## 结论"]
    if outcome.get("status") != "delivered":
        lines.append(f"**REVIEW：未给出评级。**原因：{_reason_text(outcome.get('reason', ''))}。")
        lines.append("在原因消除之前，本报告不含任何评级或交易价位。")
        return "\n".join(lines)
    thesis, proposal = snapshot.get("manager/thesis", {}), snapshot.get("trader/proposal", {})
    currency = snapshot.get("ledger/instrument", {}).get("currency", "")
    lines.append(f"- 评级：**{thesis.get('rating', _DASH)}**；建议操作：**{proposal.get('action', _DASH)}**")
    lines.append(f"- 入场价：{_num(proposal.get('entry'))}；止损价：{_num(proposal.get('stop'))}；"
                 f"目标价：{_num(proposal.get('target'))}（{currency}）")
    lines.append(f"- 仓位：{_num(proposal.get('size_pct'))}% 权益；"
                 f"预期持有：{proposal.get('horizon_days', _DASH)} 个交易日")
    rationale = proposal.get("rationale", "")
    if rationale:
        lines.append(f"- 方案说明：{rationale}")
    return "\n".join(lines)


# ---- 第 3 节：可信度 ----

def _confidence(snapshot: dict[str, Any]) -> str:
    outcome = snapshot.get("run/outcome", {})
    verified = outcome.get("facts_and_rules") == "verified"
    lines = ["## 可信度",
             "- 事实与规则：" + ("已核验。全部数字由代码计算并经第二条路径复算，风控规则由代码逐条检查。"
                              if verified else "未核验。运行被叫停，审计未确认当前版本。")]
    judgment = outcome.get("judgment", "")
    if judgment.startswith("accepted"):
        text = f"评审接受（{judgment.replace('accepted ', '')}，多数评审未能否决）。"
    elif judgment == "contested":
        text = "评审相持。多数评审反对，方案不可信。"
    elif judgment.startswith("not reviewed"):
        reason = judgment.split(":", 1)[1].strip() if ":" in judgment else ""
        text = "未经评审" + (f"（{_reason_text(reason)}）。" if reason else "。")
    else:
        text = judgment
    lines.append(f"- 模型判断：{text}")
    return "\n".join(lines)


# ---- 第 4 节：依据的事实 ----

def _facts(snapshot: dict[str, Any]) -> str:
    analysis = snapshot.get("technical/analysis", {})
    claims = analysis.get("claims", ())
    if not claims:
        return "## 依据的事实\n\n（无）"
    ledger = {f["id"]: f for f in snapshot.get("ledger/facts", [])}
    lines = ["## 依据的事实", "", "| 事实 | 含义 | 账本数值 | 数据日 | 分析师的解读 |",
             "|---|---|---|---|---|"]
    for claim in claims:
        fact = ledger.get(claim.get("fact", ""), {})
        unit = fact.get("unit", "")
        value = _num(fact.get("value")) + (f" {unit}" if unit and unit != "%" else ("%" if unit == "%" else ""))
        lines.append(f"| {claim.get('fact', _DASH)} | {fact.get('label', _DASH)} | {value} | "
                     f"{fact.get('as_of', _DASH)} | {claim.get('reading', '')} |")
    if missing := [c.get("fact") for c in claims if c.get("fact") not in ledger]:
        lines.append("")
        lines.append(f"注意：以下引用的事实不在账本中：{('、'.join(missing))}。")
    return "\n".join(lines)


# ---- 第 5 节：论点与失效条件 ----

def _thesis(snapshot: dict[str, Any]) -> str:
    thesis = snapshot.get("manager/thesis")
    if not thesis:
        return "## 论点与失效条件\n\n（无）"
    delivered = snapshot.get("run/outcome", {}).get("status") == "delivered"
    lines = ["## 论点与失效条件"]
    if delivered:
        lines.append(f"评级 {thesis.get('rating', _DASH)}，预期持有 {thesis.get('horizon_days', _DASH)} 个交易日。")
    for i, point in enumerate(thesis.get("points", ()), 1):
        lines.append(f"{i}. {point.get('text', '')}（依据：{'、'.join(point.get('cites', ()))}）")
    invalidation = thesis.get("invalidation", ())
    if invalidation:
        lines.append("")
        lines.append("失效条件：")
        lines.extend(f"- {item}" for item in invalidation)
    return "\n".join(lines)


# ---- 第 6 节：多空观点 ----

def _opinions(snapshot: dict[str, Any]) -> str:
    lines = ["## 多空观点（未经审计的观点）",
             "以下是模型扮演的多空研究员的观点，未经过审计，可能含有未经验证的数字。"]
    for key, side in (("bull/argument", "多方"), ("bear/argument", "空方")):
        argument = snapshot.get(key)
        if not argument:
            lines.append(f"\n**{side}**：（无）")
            continue
        lines.append(f"\n**{side}**")
        lines.extend(f"- {point.get('text', '')}" for point in argument.get("points", ()))
        rebuttal = argument.get("rebuttal", "")
        if rebuttal:
            lines.append(f"- 反驳：{rebuttal}")
    return "\n".join(lines)


# ---- 第 7 节：风控检查 ----

def _rules(snapshot: dict[str, Any]) -> str:
    results = snapshot.get("audit/rules", [])
    if not results:
        return "## 风控检查\n\n（无）"
    delivered = snapshot.get("run/outcome", {}).get("status") == "delivered"
    formulas = {s.get("key"): s.get("formula", "") for s in snapshot.get("rules/limits", [])}
    status_text = {"pass": "通过", "fail": "不通过", "n/a": "不适用"}
    header = "| 规则 | " + ("公式 | " if delivered else "") + "上限 | 计算值 | 结论 |"
    lines = ["## 风控检查", "", header,
             "|---|" + ("---|" if delivered else "") + "---|---|---|"]
    for rule in results:
        key = rule.get("key", _DASH)
        middle = f" {formulas.get(key, '')} |" if delivered else ""
        lines.append(f"| {key} |{middle} {_num(rule.get('limit'))} | "
                     f"{_num(rule.get('actual'))} | {status_text.get(rule.get('status'), rule.get('status'))} |")
    return "\n".join(lines)


# ---- 第 8 节：评审意见 ----

def _judges(snapshot: dict[str, Any]) -> str:
    lines = ["## 评审意见"]
    summary = snapshot.get("judges/status")
    if summary:
        label = "评审通过" if summary.startswith("accepted") else "评审相持"
        lines.append(f"汇总：{label}（{summary}）。")
    for lens, name in _JUDGE_NAME.items():
        verdict = snapshot.get(f"judge.{lens}/verdict")
        if not verdict:
            lines.append(f"- {name}：未询问。")
            continue
        word = "接受" if verdict.get("accept") else "反对"
        target = {"thesis": "论点", "levels": "价位与仓位", "none": ""}.get(verdict.get("target"), "")
        lines.append(f"- {name}：{word}{('（' + target + '）') if target else ''}。{verdict.get('critique', '')}")
    return "\n".join(lines)


# ---- 第 9 节：返工记录（需要事件日志）----

def _rework(events: Sequence[Event] | None) -> str:
    lines = ["## 返工记录"]
    versions = [(e.seq, e.data) for e in events or () if e.type == "artifact.written"
                and e.data.get("key") == "audit/findings" and e.data.get("value")]
    if not events:
        return "\n".join([*lines, "（本节需要事件日志。）"])
    if not versions:
        return "\n".join([*lines, "（无。审计一次通过，没有返工。）"])
    routes = {e.seq: e.data for e in events if e.type == "route"}
    for seq, data in versions:
        lines.append(f"\n**第 {data.get('version', _DASH)} 版被退回**（事件 {seq}）：")
        for finding in data.get("value", ()):
            owner = finding.get("owner") or ""
            lines.append(f"- [{finding.get('check', _DASH)}] {finding.get('subject', '')}："
                         f"{finding.get('message', '')}（修改人：{_STAGE_NAME.get(owner, owner)}）")
        for route_seq in sorted(r for r in routes if r > seq):
            route = routes[route_seq]
            stage = route.get("to") or ""
            lines.append(f"- 重跑：{_STAGE_NAME.get(stage, stage)}（{route.get('why', '')}）")
            break
    return "\n".join(lines)


# ---- 第 10 节：数据质量 ----

def _quality(snapshot: dict[str, Any]) -> str:
    card = snapshot.get("ledger/card")
    if not card:
        return "## 数据质量\n\n（无）"
    lines = ["## 数据质量",
             f"- 首根K线：{card.get('first_bar', _DASH)}；末根K线：{card.get('last_bar', _DASH)}；"
             f"共 {card.get('bars', _DASH)} 根",
             f"- 距分析日 {card.get('stale_days', _DASH)} 个自然日；丢弃行数：{card.get('dropped_rows', _DASH)}"]
    if gaps := card.get("gaps", ()):
        lines.append(f"- 缺口：{'；'.join(gaps)}")
    if unavailable := card.get("unavailable", ()):
        lines.append("- 不可用指标：" + "；".join(f"{u.get('id')}（{u.get('reason')}）" for u in unavailable))
    if warnings := card.get("warnings", ()):
        lines.append(f"- 警告：{'；'.join(warnings)}")
    return "\n".join(lines)


# ---- 第 11 节：用量与成本 ----

def _usage(snapshot: dict[str, Any], started: dict[str, Any]) -> str:
    rows = snapshot.get("run/usage", [])
    lines = ["## 用量与成本"]
    if not rows:
        return "\n".join([*lines, "（无模型调用。）"])
    lines += ["", "| 角色 | 模型 | 调用 | 请求 | 输入 token | 输出 token | 成本 |", "|---|---|---|---|---|---|---|"]
    tokens, cost, priced = 0, 0.0, True
    for row in rows:
        row_tokens = row.get("input_tokens", 0) + row.get("output_tokens", 0)
        tokens += row_tokens
        if row.get("cost") is None:
            priced = False
        else:
            cost += row["cost"]
        lines.append(f"| {row.get('role', _DASH)} | {row.get('model', _DASH)} | {row.get('calls', 0)} | "
                     f"{row.get('requests', 0)} | {row.get('input_tokens', 0)} | {row.get('output_tokens', 0)} | "
                     f"{_num(row.get('cost')) if row.get('cost') is not None else '未定价'} |")
    total_cost = f"{cost:.4f}".rstrip("0").rstrip(".") if priced else f"{cost:.4f}（另有未定价模型）"
    lines.append(f"| 合计 | {_DASH} | {sum(r.get('calls', 0) for r in rows)} | "
                 f"{sum(r.get('requests', 0) for r in rows)} | {tokens} | {tokens} | {total_cost} |")
    ceilings = started.get("ceilings", {})
    token_ceiling, cost_ceiling = ceilings.get("run_tokens"), ceilings.get("run_cost")
    lines.append(f"- token 合计 {tokens}，上限 {token_ceiling if token_ceiling else '不设上限'}；"
                 f"成本合计 {total_cost}，上限 {cost_ceiling if cost_ceiling else '不设上限'}。")
    return "\n".join(lines)


# ---- 第 12 节：运行信息（需要事件日志）----

def _run_info(snapshot: dict[str, Any], started: dict[str, Any], run_id: str) -> str:
    lines = ["## 运行信息"]
    if not started:
        return "\n".join([*lines, "（本节需要事件日志。）"])
    models = started.get("models", {})
    flow = started.get("flow", {})
    mode = started.get("mode") or ""
    lines.append(f"- 运行 ID：{run_id or _DASH}")
    lines.append(f"- 版本：{started.get('version', _DASH)}；模式：{_MODE_NAME.get(mode, mode)}")
    lines.append(f"- 模型：quick = {models.get('quick', _DASH)}，deep = {models.get('deep', _DASH)}；"
                 f"输出方式：{started.get('output_mode', _DASH)}")
    lines.append(f"- 流程：辩论 {flow.get('debate_rounds', _DASH)} 轮，审计 {flow.get('audit', _DASH)}，"
                 f"评审 {'开' if flow.get('judges') else '关'}，打回上限 {flow.get('audit_rounds', _DASH)}，"
                 f"评审轮数 {flow.get('judge_rounds', _DASH)}，法定数 {flow.get('quorum', _DASH)}")
    lines.append(f"- 数据集：{started.get('universe', _DASH)}")
    return "\n".join(lines)
