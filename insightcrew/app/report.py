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
_STAGES = {"technical": "技术分析", "fundamentals": "基本面分析", "disclosures": "公告分析",
           "news": "新闻分析", "sentiment": "情感分析",
           "debate": "多空辩论", "manager": "研究主管", "trader": "交易员",
           "risk": "风险辩论", "reflector": "复盘员"}
_RISK_ORDER = ("aggressive", "conservative", "neutral")      # 19.10 的发言顺序
_RISK_NAME = {"aggressive": "激进风控", "conservative": "保守风控", "neutral": "中立风控"}
_PSTATE = {"not_provided": "未提供组合", "flat": "空仓", "held": "持有", "unavailable": "不可用"}
_BAND = {"bullish": "强烈看多", "mildly_bullish": "略偏多", "neutral": "中性",
         "mixed": "多空分歧", "mildly_bearish": "略偏空", "bearish": "强烈看空"}
_TAG = {"bullish": "看多", "bearish": "看空", "none": "无标签"}
_HIT = {"stop": "止损", "target": "目标", "neither": "均未触及", "n/a": "不适用"}
_MODES = {"model": "真实模型", "scripted": "脚本化模型（回复为预先编写）", "replay": "回放"}
_TARGETS = {"thesis": "论点", "levels": "价位与仓位"}
_STATUS = {"pass": "通过", "fail": "不通过", "n/a": "不适用"}
_DIRECTION = {"positive": "正面", "negative": "负面", "neutral": "中性"}
_STANCE = {"positive": "正面", "mixed": "混合", "neutral": "中性", "negative": "负面"}
_CHANNEL_STATE = {"fundamentals": {"available": "可用", "empty": "没有已申报的年度报表",
                                   "unavailable": "不可用"},
                  "disclosures": {"available": "可用", "empty": "窗口内没有公告",
                                  "unavailable": "不可用"},
                  "news": {"available": "可用", "empty": "窗口内没有新闻", "unavailable": "不可用"},
                  "sentiment": {"available": "可用", "empty": "窗口内没有帖子", "unavailable": "不可用"},
                  "macro": {"available": "可用", "empty": "没有宏观序列", "unavailable": "不可用"}}
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
        _portfolio(snapshot),
        _confidence(snapshot),
        _facts(snapshot),
        _fundamentals(snapshot),
        _disclosures(snapshot),
        _news_macro(snapshot),
        _sentiment(snapshot),
        _lessons(snapshot),
        _thesis(snapshot, delivered),
        _opinions(snapshot),
        _risk_views(snapshot, delivered),
        _rules(snapshot, delivered),
        _judges(snapshot, delivered),
        _rework(events, delivered),
        _quality(snapshot),
        _usage(snapshot, started),
        _run_info(started, run_id),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


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


# ---- 2b 组合状态（仅在给了组合文件时出现；BLUEPRINT 19.9）----


def _portfolio(snapshot: dict[str, Any]) -> str:
    """组合状态一节。键不在黑板上（没给组合文件）时整节不出现。

    数值全部来自 portfolio/state（代码按组合文件与账本收盘价算出，不变量 3 的扩展）。
    """
    state = snapshot.get("portfolio/state")
    if not state:
        return ""
    kind = state.get("state", "")
    lines = ["## 组合状态"]
    if kind == "held":
        lines.append(f"- 状态：持有。数量 {_num(state.get('quantity'))} 股"
                     f"（{state.get('currency') or _DASH}），占权益 {_num(state.get('position_pct'))}%；"
                     f"现金占权益 {_num(state.get('cash_pct'))}%")
    elif kind == "flat":
        lines.append(f"- 状态：空仓。组合里没有这个标的；现金占权益 {_num(state.get('cash_pct'))}%"
                     f"（{state.get('currency') or _DASH}）")
    else:
        # not_provided 与 unavailable：状态加一句代码写下的原因
        lines.append(f"- 状态：{_PSTATE.get(kind, kind)}"
                     + (f"（{state.get('reason')}）" if state.get("reason") else ""))
    return "\n".join(lines)


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
    tools = list(snapshot.get("tools/facts") or [])
    if not claims and not tools:
        return "## 依据的事实\n\n（无）"
    ledger = {fact["id"]: fact for fact in snapshot.get("ledger/facts", [])}
    by_tool = {fact.get("id"): fact for fact in tools}        # 工具事实（19.11）：经两条路径计算并比对
    rows = []
    for claim in claims:
        fid = claim.get("fact", "")
        fact = ledger.get(fid) or by_tool.get(fid, {})
        from_tool = fid in by_tool
        rows.append((f"{fid}（工具）" if from_tool else fid,
                     fact.get("label") or ("（工具按需计算）" if from_tool else "（账本中没有这个事实）"),
                     _with_unit(fact.get("value"), fact.get("unit", "")), fact.get("as_of", _DASH),
                     claim.get("reading", "")))
    cited = {claim.get("fact") for claim in claims}
    rows += [(f"{fact.get('id', _DASH)}（工具）", "（工具按需计算）",
              _with_unit(fact.get("value"), fact.get("unit", "")), fact.get("as_of", _DASH), _DASH)
             for fact in tools if fact.get("id") not in cited]
    lines = ["## 依据的事实", "",
             "数值与数据日取自事实账本，不取模型的转述。", ""]
    if tools:
        lines[2] = ("数值与数据日取自事实账本，不取模型的转述。标有（工具）的行是技术分析师"
                    "按需请求的指标，同样经两条路径计算并逐条比对。")
    lines += _table(("事实", "含义", "账本数值", "数据日", "分析师的解读"), rows)
    return "\n".join(lines)


# ---- 4b 基本面事实（仅在通道打开且上黑板时出现）----


def _fundamentals(snapshot: dict[str, Any]) -> str:
    if not snapshot.get("fundamentals/facts"):
        return ""              # 通道没开、不可用或没有事实：整节不出现，状态见数据质量一节
    claims = snapshot.get("fundamentals/analysis", {}).get("claims", ())
    facts = {fact["id"]: fact for fact in snapshot.get("fundamentals/facts", [])}
    rows = []
    for claim in claims:
        fact = facts.get(claim.get("fact", ""), {})
        rows.append((claim.get("fact", _DASH), fact.get("label", "（基本面事实里没有这个 ID）"),
                     _with_unit(fact.get("value"), fact.get("unit", "")), fact.get("as_of", _DASH),
                     claim.get("reading", "")))
    lines = ["## 基本面事实", "",
             "数值与申报日取自基本面事实账本（经第二条路径复算），不取模型的转述。", "",
             *_table(("事实", "含义", "申报值", "申报日", "分析师的解读"), rows)]
    card = snapshot.get("fundamentals/card")
    if card:
        lines += ["", f"- 数据卡：来源 {card.get('source', _DASH)}，申报货币 {card.get('currency', _DASH)}，"
                      f"最近财年期末 {card.get('fiscal_year_end', _DASH)}，"
                      f"所引最晚申报 {card.get('filed', _DASH)}，共 {card.get('rows', _DASH)} 行"]
        if card.get("unavailable"):
            lines.append("- 不可用：" + "；".join(f"{u.get('id')}（{u.get('reason')}）"
                                                  for u in card["unavailable"]))
        if card.get("warnings"):
            lines.append("- 重述提醒：" + "；".join(card["warnings"]))
    return "\n".join(lines)


# ---- 4c 公告条目（来源原文，未经核验）----


def _disclosures(snapshot: dict[str, Any]) -> str:
    if not snapshot.get("disclosures/status"):
        return ""
    items = snapshot.get("disclosures/items", [])
    signals = snapshot.get("disclosures/analysis", {}).get("signals", ())
    lines = ["## 公告条目（来源原文，未经核验）", "",
             "以下标题与类别是来源的原文。程序只核对每条存在且发布于分析日之前；内容读得对不对由评审判断。"]
    if not items:
        lines.append("（窗口内没有公告。）")
    else:
        rows = [(item.get("id", _DASH), item.get("published", _DASH), item.get("source", _DASH),
                 item.get("kind", _DASH), item.get("title", _DASH)) for item in items]
        lines += ["", *_table(("编号", "发布日", "来源", "类别", "标题"), rows)]
    if signals:
        lines += ["", "分析师的信号："]
        lines += [f"- {s.get('item', _DASH)}：{_DIRECTION.get(s.get('direction'), s.get('direction'))}"
                  f"——{s.get('reading', '')}" for s in signals]
    analysis = snapshot.get("disclosures/analysis", {})
    if analysis:
        lines += ["", f"窗口内公告合起来的基调：{_STANCE.get(analysis.get('stance'), analysis.get('stance'))}"
                      f"（把握：{analysis.get('confidence', _DASH)}）"]
    card = snapshot.get("disclosures/card")
    if card:
        lines.append(f"- 窗口 {card.get('window_from', _DASH)} 到 {card.get('window_to', _DASH)}，"
                     f"列出 {card.get('items', _DASH)} 条；无日期被丢弃 {card.get('dropped', _DASH)} 条，"
                     f"超出上限被截断 {card.get('truncated', _DASH)} 条")
    return "\n".join(lines)


# ---- 4d 新闻与宏观（新闻通道打开时出现；标题与市场行情是来源原文，宏观数值是事实）----


def _news_macro(snapshot: dict[str, Any]) -> str:
    """新闻条目、宏观事实与预测市场。news/status 不在黑板上（通道没开）时整节不出现。"""
    status = snapshot.get("news/status")
    if not status:
        return ""
    lines = ["## 新闻与宏观", ""]
    state = status.get("state", "")
    if state == "unavailable":
        lines.append(f"新闻通道不可用（{status.get('reason', _DASH)}）。")
    else:
        lines.append("以下标题是来源的原文。程序只核对每条存在且发布于分析日之前；内容读得对不对由评审判断。")
        items = snapshot.get("news/items") or []
        if not items:
            lines.append("（窗口内没有新闻。）")
        else:
            rows = [(item.get("id", _DASH), item.get("published", _DASH), item.get("source", _DASH),
                     item.get("title", _DASH)) for item in items]
            lines += ["", *_table(("编号", "发布日", "来源", "标题"), rows)]
        analysis = snapshot.get("news/analysis", {})
        signals = analysis.get("signals") or ()
        if signals:
            lines += ["", "分析师的信号："]
            lines += [f"- {s.get('item', _DASH)}：{_DIRECTION.get(s.get('direction'), s.get('direction'))}"
                      f"——{s.get('reading', '')}" for s in signals]
        if analysis:
            lines += ["", f"窗口内新闻合起来的基调：{_STANCE.get(analysis.get('stance'), analysis.get('stance'))}"
                          f"（把握：{analysis.get('confidence', _DASH)}）"]
        card = snapshot.get("news/card")
        if card:
            lines.append(f"- 窗口 {card.get('window_from', _DASH)} 到 {card.get('window_to', _DASH)}，"
                         f"列出 {card.get('items', _DASH)} 条；无日期被丢弃 {card.get('dropped', _DASH)} 条，"
                         f"超出上限被截断 {card.get('truncated', _DASH)} 条")
    macro = _macro_part(snapshot)
    if macro:
        lines += ["", *macro]
    return "\n".join(lines)


def _macro_part(snapshot: dict[str, Any]) -> list[str]:
    """宏观事实表与预测市场列表；宏观通道没开（macro/status 不在）时一块都没有。"""
    status = snapshot.get("macro/status")
    if not status:
        return []
    if status.get("state") == "unavailable":
        return [f"宏观通道不可用（{status.get('reason', _DASH)}）。"]
    lines: list[str] = []
    facts = snapshot.get("macro/facts") or []
    if facts:
        claims = {c.get("fact"): c.get("reading", "")
                  for c in snapshot.get("news/analysis", {}).get("claims", ())}
        rows = [(fact.get("id", _DASH), fact.get("label", _DASH),
                 _with_unit(fact.get("value"), fact.get("unit", "")), fact.get("as_of", _DASH),
                 claims.get(fact.get("id"), _DASH)) for fact in facts]
        lines += ["宏观事实（经两条路径复算，数值不取模型的转述）：", "",
                  *_table(("事实", "含义", "数值", "数据日", "分析师的解读"), rows)]
    card = snapshot.get("macro/card")
    if card:
        note = f"- 宏观数据卡：来源 {card.get('source', _DASH)}"
        if card.get("series") is not None:
            note += f"，读了 {card.get('series')} 条序列"
        if card.get("unavailable"):
            note += "；不可用：" + "；".join(f"{u.get('id')}（{u.get('reason')}）"
                                           for u in card["unavailable"])
        lines.append(note)
    markets = snapshot.get("macro/markets") or []
    if markets:
        lines += ["", "预测市场（来源原话，未经核验）：",
                  "问题与概率照录自来源，程序不做任何核对；概率为来源给的隐含概率（0 到 1）。", ""]
        rows = [(m.get("id", _DASH), m.get("question", _DASH), _num(m.get("probability")),
                 _num(m.get("volume")), m.get("end_date", _DASH)) for m in markets]
        lines += _table(("编号", "问题", "隐含概率", "成交量", "截止日"), rows)
    if not lines:
        lines.append("（宏观通道没有可展示的内容。）")
    return lines


# ---- 4e 社交情感（情感通道打开时出现；帖子正文是来源原文）----


def _sentiment(snapshot: dict[str, Any]) -> str:
    status = snapshot.get("sentiment/status")
    if not status:
        return ""
    lines = ["## 社交情感", ""]
    if status.get("state") == "unavailable":
        lines.append(f"情感通道不可用（{status.get('reason', _DASH)}）。")
        return "\n".join(lines)
    lines.append("以下是来源的原文。程序只核对每条存在且发布于分析日之前；情绪带与分数是分析师的判断。")
    analysis = snapshot.get("sentiment/analysis", {})
    if analysis:
        lines += ["", f"- 情绪带：**{_BAND.get(analysis.get('band'), analysis.get('band'))}**，"
                      f"情绪分 {_num(analysis.get('score'))}（0 最看空，10 最看多）；"
                      f"把握：{analysis.get('confidence', _DASH)}"]
        signals = analysis.get("signals") or ()
        if signals:
            lines.append("分析师的信号：")
            lines += [f"- {s.get('post', _DASH)}：{_DIRECTION.get(s.get('direction'), s.get('direction'))}"
                      f"——{s.get('reading', '')}" for s in signals]
    posts = snapshot.get("sentiment/posts") or []
    if not posts:
        lines.append("（窗口内没有帖子。）")
    else:
        rows = [(post.get("id", _DASH), post.get("published", _DASH), post.get("source", _DASH),
                 _TAG.get(post.get("tag"), post.get("tag")), post.get("text", _DASH)) for post in posts]
        lines += ["", *_table(("编号", "发布日", "来源", "自带标签", "内容"), rows)]
    card = snapshot.get("sentiment/card")
    if card:
        lines.append(f"- 窗口 {card.get('window_from', _DASH)} 到 {card.get('window_to', _DASH)}，"
                     f"列出 {card.get('items', _DASH)} 条；无日期被丢弃 {card.get('dropped', _DASH)} 条，"
                     f"超出上限被截断 {card.get('truncated', _DASH)} 条")
    return "\n".join(lines)


# ---- 4f 用到的课程（记忆打开时出现；BLUEPRINT 19.13）----


def _lessons(snapshot: dict[str, Any]) -> str:
    """研究主管这次看到的课程。memory/lessons 不在黑板上（记忆没开）时整节不出现。

    行里的数字来自已结算的结果（代码按持有期 K 线算出，不变量 3 的扩展）；
    只有结果可知日不晚于分析日的课程才会在这里（不变量 20）。
    """
    if "memory/lessons" not in snapshot:
        return ""
    lessons = snapshot.get("memory/lessons") or []
    lines = ["## 用到的课程", ""]
    if not lessons:
        status = snapshot.get("memory/status") or {}
        reason = status.get("reason", "")
        lines.append(f"（没有分析日之前已经知道结果的课程{f'：{reason}' if reason else ''}。）")
        return "\n".join(lines)
    lines.append("以下是研究主管在这次分析里看到的课程；每条连同它依据的已结算结果。")
    rows = [(lesson.get("ticker", _DASH), lesson.get("as_of", _DASH), lesson.get("known_on", _DASH),
             lesson.get("rating", _DASH), f"{_num(lesson.get('return_pct'))}%",
             _DASH if lesson.get("alpha_pct") is None else f"{_num(lesson.get('alpha_pct'))}%",
             _HIT.get(lesson.get("hit"), lesson.get("hit")), lesson.get("lesson", ""))
            for lesson in lessons]
    lines += ["", *_table(("代码", "分析日", "结果可知于", "当时评级", "收益率", "超额收益", "触及", "课程"), rows)]
    return "\n".join(lines)


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


# ---- 6b 风险三方意见（未经审计的意见；risk 节点打开时出现，BLUEPRINT 19.10）----


def _risk_views(snapshot: dict[str, Any], delivered: bool) -> str:
    """三位风控的意见。任何一个 risk/* 键不在时整节不出现。

    只展示立场与批评：他们建议的仓位与价位不是审计对象，不进报告——
    报告里的价位与仓位始终是审计通过的那一版方案。
    """
    views = [(stance, snapshot.get(f"risk/{stance}")) for stance in _RISK_ORDER]
    if not any(view for _, view in views):
        return ""
    lines = ["## 风险三方意见（未经审计的意见）",
             "三位风控在审计之前评论交易员的方案；辩论只能通过交易员的修订改变方案。",
             "报告展示的价位与仓位是审计通过的那一版方案，不是这里的建议。"]
    for stance, view in views:
        name = _RISK_NAME[stance]
        if not view:
            lines.append(f"- {name}：未发言。")
            continue
        line = f"- {name}："
        if delivered and view.get("critique"):
            lines.append(line + view["critique"])
        else:
            lines.append(line + "发表了意见。")
    if not delivered:
        lines += ["", _WITHHELD]
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
    for channel, name in (("fundamentals", "基本面"), ("disclosures", "公告"),
                          ("news", "新闻"), ("sentiment", "情感"), ("macro", "宏观")):
        status = snapshot.get(f"{channel}/status")
        if not status:
            continue                                # 通道没开：不加这一行，报告与 2.0 相同
        state = _CHANNEL_STATE[channel].get(status.get("state"), status.get("state"))
        line = f"- {name}通道：{state}"
        if status.get("state") != "available" and status.get("reason"):
            line += f"（{status['reason']}）"
        lines.append(line)
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
    lines = [
        "## 用量与成本", "",
        *_table(("角色", "模型", "调用", "请求", "输入 token", "输出 token", "成本"), body), "",
        f"- token 合计 {total_in + total_out}，上限 {ceilings.get('run_tokens') or '不设上限'}；"
        f"成本合计 {total_cost}，上限 {ceilings.get('run_cost') or '不设上限'}。",
        "- “请求”多于“调用”的部分，是输出不合契约后的纠正重试。"]
    wall = snapshot.get("run/wall_ms")
    if wall:                                         # 每分析师墙钟（19.5 第 5 条）：黑板没有这个键就不展示
        lines.append("- 各段用时：" + "、".join(f"{_STAGES.get(node, node)} {_num(ms)} 毫秒"
                                                    for node, ms in wall.items()))
    return "\n".join(lines)


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
