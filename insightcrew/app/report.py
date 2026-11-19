"""报告：把黑板快照渲染成中文 Markdown。它只读，不计算。

报告里的行情数字只有两个来源：账本里的事实（按 ID 取值，从不取模型的转述），
和已通过审计的方案。数值只做展示格式化。唯一的求和是第 11 节的模型用量合计。

REVIEW 的报告不描述那份没有交付的方案：不出现评级、价位、论点正文、评审意见正文，
也不出现审计发现的说明文字（它们会复述价位与评级）。这些内容都还在 journal.jsonl 里，
运行页面的过程记录可以逐条查看。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ..kernel.events import Event

_REASON = {
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
_JUDGES = {"evidence": "证据评审", "conservative": "保守评审", "aggressive": "进取评审"}
_STAGES = {"technical": "技术分析", "debate": "多空辩论", "manager": "研究主管", "trader": "交易员"}
_MODES = {"model": "真实模型", "scripted": "脚本化模型（回复为预先编写）", "replay": "回放"}
_TARGETS = {"thesis": "论点", "levels": "价位与仓位"}
_STATUS = {"pass": "通过", "fail": "不通过", "n/a": "不适用"}
_DASH = "—"
_WITHHELD = "本次运行没有交付结论，这部分内容不在报告里展示；它仍在 journal.jsonl 中，可在运行页面的过程记录里查看。"


def render(snapshot: dict[str, Any], events: Sequence[Event] | None = None, run_id: str = "") -> str:
    """把一次运行的黑板快照渲染成报告。第 9、12 节与停机说明需要事件日志；不给时这些部分从简。"""
    events = events or ()
    started = next((e.data for e in events if e.type == "run.started"), {})
    delivered = snapshot.get("run/outcome", {}).get("status") == "delivered"
    sections = [
        _title(snapshot, started),
        _conclusion(snapshot, events, delivered),
        _confidence(snapshot),
        _facts(snapshot),
        _thesis(snapshot, delivered),
        _opinions(snapshot),
        _rules(snapshot, delivered),
        _judges(snapshot, delivered),
        _rework(events, delivered),
        _quality(snapshot),
        _usage(snapshot, started),
        _run_info(started, run_id),
    ]
    return "\n\n".join(sections) + "\n"


# ---- 展示格式：不做运算 ----

def _num(value: Any, places: int = 4) -> str:
    """数值的展示格式：至多 places 位小数，去掉多余的零。"""
    if value is None:
        return _DASH
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-", "-0") else text


def _with_unit(value: Any, unit: str) -> str:
    if value is None:
        return _DASH
    return _num(value) + ("%" if unit == "%" else f" {unit}" if unit else "")


def _reason(reason: str) -> str:
    return _REASON.get(reason, reason)


def _cell(text: Any) -> str:
    """表格单元格里的文字：竖线与换行会破坏表格。"""
    return str(text).replace("|", "｜").replace("\n", " ")


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows]
    return lines


# ---- 1 标题 ----

def _title(snapshot: dict[str, Any], started: dict[str, Any]) -> str:
    scope = snapshot.get("request/scope", {})
    instrument = snapshot.get("ledger/instrument", {})
    request = started.get("request", {})
    ticker = instrument.get("ticker") or scope.get("ticker") or request.get("ticker") or _DASH
    as_of = scope.get("as_of") or request.get("as_of") or _DASH
    name = f" {instrument['name']}" if instrument.get("name") else ""
    return (f"# {ticker}{name} 分析报告（分析日 {as_of}）\n\n"
            "本报告由课程项目自动生成，不构成投资建议；系统不执行、不路由、也不模拟任何订单。")


# ---- 2 结论 ----

def _conclusion(snapshot: dict[str, Any], events: Sequence[Event], delivered: bool) -> str:
    outcome = snapshot.get("run/outcome", {})
    lines = ["## 结论"]
    if not delivered:
        lines.append(f"**REVIEW：未给出评级。**原因：{_reason(outcome.get('reason', ''))}。")
        lines += [f"- {message}" for message in _refusal_notes(events)]
        lines.append("在原因消除之前，本报告不含任何评级或交易价位。")
        return "\n".join(lines)
    thesis, proposal = snapshot.get("manager/thesis", {}), snapshot.get("trader/proposal", {})
    currency = snapshot.get("ledger/instrument", {}).get("currency", "")
    lines.append(f"- 评级：**{thesis.get('rating', _DASH)}**；建议操作：**{proposal.get('action', _DASH)}**")
    lines.append(f"- 入场价：{_num(proposal.get('entry'))}；止损价：{_num(proposal.get('stop'))}；"
                 f"目标价：{_num(proposal.get('target'))}（{currency}）")
    lines.append(f"- 仓位：{_num(proposal.get('size_pct'))}% 权益；"
                 f"预期持有：{proposal.get('horizon_days', _DASH)} 个交易日")
    if proposal.get("rationale"):
        lines.append(f"- 方案说明：{proposal['rationale']}")
    return "\n".join(lines)


def _refusal_notes(events: Sequence[Event]) -> list[str]:
    """准入或账本闸门拦下运行时留下的说明。它们说的是数据，不是方案，所以可以进 REVIEW 报告。"""
    return [item.get("message", "") for e in events
            if e.type == "gate.checked" and e.data.get("gate") in ("intake", "ledger")
            for item in e.data.get("detail") or () if item.get("message")]


# ---- 3 可信度 ----

def _confidence(snapshot: dict[str, Any]) -> str:
    outcome = snapshot.get("run/outcome", {})
    if outcome.get("facts_and_rules") == "verified":
        checked = "已核验。全部数字由代码计算并经第二条路径复算，风控规则由代码逐条检查。"
    else:
        checked = "未核验。运行被叫停，审计没有确认当前版本。"
    return "\n".join(["## 可信度", f"- 事实与规则：{checked}",
                      f"- 模型判断：{_judgment(outcome.get('judgment', ''))}"])


def _judgment(judgment: str) -> str:
    if judgment.startswith("accepted"):
        return f"评审接受（{judgment.removeprefix('accepted').strip()}）。评审只能否决，不能证明方案正确。"
    if judgment == "contested":
        return "评审相持。多数评审反对，方案不可信。"
    if judgment.startswith("not reviewed"):
        why = judgment.partition(":")[2].strip()
        return f"未经评审（{'评审已关闭' if why == 'judges disabled' else _reason(why)}）。" if why else "未经评审。"
    return judgment or _DASH


# ---- 4 依据的事实 ----

def _facts(snapshot: dict[str, Any]) -> str:
    claims = snapshot.get("technical/analysis", {}).get("claims", ())
    if not claims:
        return "## 依据的事实\n\n（无）"
    ledger = {fact["id"]: fact for fact in snapshot.get("ledger/facts", [])}
    rows = []
    for claim in claims:
        fact = ledger.get(claim.get("fact", ""), {})
        rows.append((claim.get("fact", _DASH), fact.get("label", "（账本中没有这个事实）"),
                     _with_unit(fact.get("value"), fact.get("unit", "")), fact.get("as_of", _DASH),
                     claim.get("reading", "")))
    return "\n".join(["## 依据的事实", "",
                      "数值与数据日取自事实账本，不取模型的转述。", "",
                      *_table(("事实", "含义", "账本数值", "数据日", "分析师的解读"), rows)])


# ---- 5 论点与失效条件 ----

def _thesis(snapshot: dict[str, Any], delivered: bool) -> str:
    thesis = snapshot.get("manager/thesis")
    if not thesis:
        return "## 论点与失效条件\n\n（无）"
    if not delivered:
        return f"## 论点与失效条件\n\n{_WITHHELD}"
    lines = ["## 论点与失效条件",
             f"评级 {thesis.get('rating', _DASH)}，预期持有 {thesis.get('horizon_days', _DASH)} 个交易日。"]
    if thesis.get("summary"):
        lines.append(thesis["summary"])
    lines.append("")
    lines += [f"{i}. {point.get('text', '')}（依据：{'、'.join(point.get('cites', ()))}）"
              for i, point in enumerate(thesis.get("points", ()), 1)]
    if thesis.get("invalidation"):
        lines += ["", "失效条件："]
        lines += [f"- {item}" for item in thesis["invalidation"]]
    return "\n".join(lines)


# ---- 6 多空观点 ----

def _opinions(snapshot: dict[str, Any]) -> str:
    lines = ["## 多空观点（未经审计的观点）",
             "以下是模型扮演的多空研究员的观点。它们没有经过审计，其中的数字不保证与账本一致。"]
    for key, side in (("bull/argument", "多方"), ("bear/argument", "空方")):
        argument = snapshot.get(key)
        if not argument:
            lines += ["", f"**{side}**：（无）"]
            continue
        lines += ["", f"**{side}**"]
        lines += [f"- {point.get('text', '')}" for point in argument.get("points", ())]
        if argument.get("rebuttal"):
            lines.append(f"- 回应对方：{argument['rebuttal']}")
    return "\n".join(lines)


# ---- 7 风控检查 ----

def _rules(snapshot: dict[str, Any], delivered: bool) -> str:
    results = snapshot.get("audit/rules", [])
    if not results:
        return "## 风控检查\n\n（无）"
    if delivered:      # 公式一栏只进已交付的报告：有一条规则的公式写着评级的名字
        formulas = {spec.get("key"): spec.get("formula", "") for spec in snapshot.get("rules/limits", [])}
        header = ("规则", "公式", "界限", "计算值", "结论")
        rows = [(r.get("key", _DASH), formulas.get(r.get("key"), ""), _num(r.get("limit")),
                 _num(r.get("actual")), _STATUS.get(r.get("status"), r.get("status"))) for r in results]
    else:
        header = ("规则", "界限", "计算值", "结论")
        rows = [(r.get("key", _DASH), _num(r.get("limit")), _num(r.get("actual")),
                 _STATUS.get(r.get("status"), r.get("status"))) for r in results]
    return "\n".join(["## 风控检查", "", "计算值由代码按规则手册算出，模型不提供任何派生数字。", "",
                      *_table(header, rows)])


# ---- 8 评审意见 ----

def _judges(snapshot: dict[str, Any], delivered: bool) -> str:
    lines = ["## 评审意见"]
    summary = snapshot.get("judges/status")
    if summary:
        lines.append(f"汇总：{'评审通过' if summary.startswith('accepted') else '评审相持'}（{summary}）。")
    for lens, name in _JUDGES.items():
        verdict = snapshot.get(f"judge.{lens}/verdict")
        if not verdict:
            lines.append(f"- {name}：未询问。")
            continue
        target = _TARGETS.get(verdict.get("target"), "")
        line = f"- {name}：{'接受' if verdict.get('accept') else '反对'}" + (f"（{target}）" if target else "") + "。"
        if delivered and verdict.get("critique"):
            line += verdict["critique"]
        lines.append(line)
    if not delivered and summary:
        lines += ["", _WITHHELD]
    return "\n".join(lines)


# ---- 9 返工记录（需要事件日志）----

def _rework(events: Sequence[Event], delivered: bool) -> str:
    lines = ["## 返工记录"]
    if not events:
        return "\n".join([*lines, "（本节需要事件日志。）"])
    audits = 0
    for event in events:
        if event.type == "artifact.written" and event.data.get("key") == "audit/findings":
            audits += 1
            findings = event.data.get("value") or ()
            if findings:
                lines += ["", f"**第 {audits} 次审计未通过**（事件 {event.seq}）："]
                lines += [_finding_line(finding, delivered) for finding in findings]
        elif event.type == "route":
            stage = event.data.get("to", "")
            lines.append(f"- 重跑：{_STAGES.get(stage, stage)}及其后各段（{event.data.get('why', '')}）")
    if len(lines) == 1:
        return "\n".join([*lines, "（无。审计一次通过，评审没有要求返工。）"])
    lines.insert(1, "以下是被退回的版本上的问题，不是结论。")
    return "\n".join(lines)


def _finding_line(finding: dict[str, Any], delivered: bool) -> str:
    owner = finding.get("owner", "")
    head = f"- [{finding.get('check', _DASH)}] {finding.get('subject', '')}（修改人：{_STAGES.get(owner, owner)}）"
    if delivered:
        return f"{head}：{finding.get('message', '')}"
    if finding.get("limit") is not None or finding.get("actual") is not None:
        return f"{head}：界限 {_num(finding.get('limit'))}，实际 {_num(finding.get('actual'))}"
    return head


# ---- 10 数据质量 ----

def _quality(snapshot: dict[str, Any]) -> str:
    card = snapshot.get("ledger/card")
    if not card:
        return "## 数据质量\n\n（无）"
    lines = ["## 数据质量",
             f"- 首根 K 线：{card.get('first_bar', _DASH)}；末根 K 线：{card.get('last_bar', _DASH)}；"
             f"共 {card.get('bars', _DASH)} 根",
             f"- 末根 K 线距分析日 {card.get('stale_days', _DASH)} 个自然日；"
             f"丢弃的行：{card.get('dropped_rows', _DASH)}"]
    if card.get("gaps"):
        lines.append(f"- 缺口：{'；'.join(card['gaps'])}")
    if card.get("unavailable"):
        lines.append("- 不可用的事实：" + "；".join(f"{u.get('id')}（{u.get('reason')}）"
                                               for u in card["unavailable"]))
    if card.get("warnings"):
        lines.append(f"- 警告：{'；'.join(card['warnings'])}")
    return "\n".join(lines)


# ---- 11 用量与成本 ----

def _usage(snapshot: dict[str, Any], started: dict[str, Any]) -> str:
    rows = snapshot.get("run/usage", [])
    if not rows:
        return "## 用量与成本\n\n（没有发生模型调用。）"
    # 报告里唯一的求和：模型用量的合计。成本只合计查得到价格的那些行。
    total_in = sum(row.get("input_tokens", 0) for row in rows)
    total_out = sum(row.get("output_tokens", 0) for row in rows)
    priced = [row["cost"] for row in rows if row.get("cost") is not None]
    if not priced:
        total_cost = "未定价"
    else:
        total_cost = _num(sum(priced), 6) + ("" if len(priced) == len(rows) else "（另有未定价的调用）")
    body = [(row.get("role", _DASH), row.get("model", _DASH), row.get("calls", 0), row.get("requests", 0),
             row.get("input_tokens", 0), row.get("output_tokens", 0),
             "未定价" if row.get("cost") is None else _num(row["cost"], 6)) for row in rows]
    body.append(("合计", _DASH, sum(row.get("calls", 0) for row in rows),
                 sum(row.get("requests", 0) for row in rows), total_in, total_out, total_cost))
    ceilings = started.get("ceilings", {})
    return "\n".join([
        "## 用量与成本", "",
        *_table(("角色", "模型", "调用", "请求", "输入 token", "输出 token", "成本"), body), "",
        f"- token 合计 {total_in + total_out}，上限 {ceilings.get('run_tokens') or '不设上限'}；"
        f"成本合计 {total_cost}，上限 {ceilings.get('run_cost') or '不设上限'}。",
        "- “请求”多于“调用”的部分，是输出不合契约后的纠正重试。"])


# ---- 12 运行信息（需要事件日志）----

def _run_info(started: dict[str, Any], run_id: str) -> str:
    if not started:
        return "## 运行信息\n\n（本节需要事件日志。）"
    models, flow, mode = started.get("models", {}), started.get("flow", {}), started.get("mode", "")
    return "\n".join([
        "## 运行信息",
        f"- 运行 ID：{run_id or _DASH}",
        f"- 版本：{started.get('version', _DASH)}；模式：{_MODES.get(mode, mode)}",
        f"- 模型：quick = {models.get('quick', _DASH)}，deep = {models.get('deep', _DASH)}；"
        f"输出方式：{started.get('output_mode', _DASH)}",
        f"- 流程：辩论 {flow.get('debate_rounds', _DASH)} 轮，审计 {flow.get('audit', _DASH)}，"
        f"评审{'开' if flow.get('judges') else '关'}，审计打回上限 {flow.get('audit_rounds', _DASH)} 次，"
        f"评审至多 {flow.get('judge_rounds', _DASH)} 轮，通过需 {flow.get('quorum', _DASH)} 票",
        f"- 数据集：{started.get('universe', _DASH)}"])
