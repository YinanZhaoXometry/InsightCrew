"""报告渲染的测试：数值取自账本而不是模型的转述；REVIEW 的报告不描述那份没有交付的方案。"""
from __future__ import annotations

from insightcrew.app.report import render
from insightcrew.kernel.events import Event

RATING_WORDS = ("Buy", "Sell", "Overweight", "Underweight")
LEVELS = ("100.5", "98.5", "104.5")


def _facts() -> list[dict]:
    return [
        {"id": "close", "label": "收盘价", "value": 100.0, "unit": "USD", "as_of": "2026-09-30",
         "source": "indicators.last", "bars_used": 300},
        {"id": "atr_14", "label": "14日平均真实波幅", "value": 2.0, "unit": "USD", "as_of": "2026-09-30",
         "source": "indicators.atr", "bars_used": 15},
        {"id": "rsi_14", "label": "14日相对强弱", "value": 61.3, "unit": "", "as_of": "2026-09-30",
         "source": "indicators.rsi", "bars_used": 15},
        {"id": "ret_20", "label": "20日涨跌幅", "value": 4.25, "unit": "%", "as_of": "2026-09-30",
         "source": "indicators.ret_pct", "bars_used": 21},
    ]


def _delivered() -> dict:
    return {
        "request/scope": {"ticker": "ACME", "as_of": "2026-09-30", "universe": "synthetic", "requested": "ACME"},
        "ledger/instrument": {"ticker": "ACME", "name": "Acme Robotics（合成数据）", "market": "US",
                              "currency": "USD", "benchmark": "SYNUS", "lot": 1, "synthetic": True},
        "ledger/facts": _facts(),
        "ledger/card": {"first_bar": "2025-01-02", "last_bar": "2026-09-30", "bars": 440, "stale_days": 0,
                        "dropped_rows": 0, "gaps": (), "unavailable": (), "warnings": ()},
        "rules/limits": [{"key": "max_risk_per_trade_pct", "limit": 1.0, "formula": "(entry−stop)/entry×size_pct",
                          "desc": "单笔风险", "source": "风控规则 2.1"},
                         {"key": "action_matches_rating", "limit": None, "formula": "Buy 只能配 Buy 或 Overweight 评级",
                          "desc": "动作与评级一致", "source": "交易规则 3.1"}],
        "technical/analysis": {"claims": (
            {"fact": "close", "value": 105.0, "reading": "价格站上均线"},          # 转述错了，报告必须显示账本的 100
            {"fact": "rsi_14", "value": 61.3, "reading": "动能偏强 | 未到超买"},
            {"fact": "ret_20", "value": 4.25, "reading": "近一月上涨"}),
            "narrative": "整体偏多。"},
        "bull/argument": {"points": ({"text": "动能强，可以 Buy", "cites": ("rsi_14",)},), "rebuttal": ""},
        "bear/argument": {"points": ({"text": "波动大", "cites": ("atr_14",)},), "rebuttal": "多方忽略了波动"},
        "manager/thesis": {"rating": "Buy", "horizon_days": 20,
                           "points": ({"text": "上行趋势完好", "cites": ("rsi_14",)},),
                           "invalidation": ("收盘跌破 98.5",), "summary": "顺势做多"},
        "trader/proposal": {"action": "Buy", "entry": 100.5, "stop": 98.5, "target": 104.5, "size_pct": 5.0,
                            "horizon_days": 20, "cites": ("atr_14",), "rationale": "止损放在一个 ATR 之外"},
        "audit/rules": [
            {"key": "action_matches_rating", "status": "pass", "limit": None, "actual": None,
             "message": "交易动作 Buy 与评级 Buy 相符"},
            {"key": "max_risk_per_trade_pct", "status": "pass", "limit": 1.0, "actual": 0.1, "message": "在限内"},
            {"key": "cash_sufficient", "status": "n/a", "limit": None, "actual": None, "message": "未给出现金"},
        ],
        "judge.evidence/verdict": {"accept": True, "target": "none", "critique": "证据充分", "cites": ("rsi_14",)},
        "judge.conservative/verdict": {"accept": True, "target": "none", "critique": "止损 98.5 合理", "cites": ()},
        "judge.aggressive/verdict": {"accept": False, "target": "levels", "critique": "仓位过小", "cites": ()},
        "judges/status": "accepted 2/3",
        "run/outcome": {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                        "judgment": "accepted 2/3"},
        "run/usage": [{"role": "technical", "model": "scripted", "calls": 1, "requests": 1,
                       "input_tokens": 1000, "output_tokens": 300, "cost": None},
                      {"role": "trader", "model": "priced-model", "calls": 1, "requests": 2,
                       "input_tokens": 2000, "output_tokens": 600, "cost": 0.004}],
    }


def _review(reason: str = "contested", judgment: str = "contested") -> dict:
    return {**_delivered(), "judges/status": "contested",
            "run/outcome": {"status": "review", "reason": reason, "facts_and_rules": "verified",
                            "judgment": judgment}}


def _started() -> list[Event]:
    return [Event(seq=1, ts="2026-09-30T10:00:00Z", type="run.started", actor="app", data={
        "request": {"ticker": "ACME", "as_of": "2026-09-30"},
        "version": "2.0.0", "mode": "scripted", "models": {"quick": "scripted", "deep": "scripted"},
        "output_mode": "auto", "flow": {"debate_rounds": 1, "audit": "on", "judges": True,
                                        "audit_rounds": 3, "judge_rounds": 2, "quorum": 2},
        "universe": "synthetic", "ceilings": {"run_tokens": 200000, "run_cost": None, "roles": {}},
        "limits": {}})]


def _rework_events() -> list[Event]:
    finding = {"check": "stop_side", "layer": "L3", "owner": "trader", "subject": "proposal.stop",
               "message": "止损 101.5 不低于入场价 100.5，方向错误", "limit": None, "actual": None}
    risk = {"check": "max_risk_per_trade_pct", "layer": "L3", "owner": "trader", "subject": "proposal",
            "message": "单笔风险 1.4%，超过上限 1%", "limit": 1.0, "actual": 1.4}
    return [*_started(),
            Event(seq=2, ts="", type="artifact.written", actor="audit", data={
                "key": "audit/findings", "version": 1, "value": [finding, risk]}),
            Event(seq=3, ts="", type="route", actor="flow", data={"to": "trader", "why": "审计发现 2 个问题"}),
            Event(seq=4, ts="", type="artifact.written", actor="audit", data={
                "key": "audit/findings", "version": 2, "value": []}),
            Event(seq=5, ts="", type="route", actor="flow", data={"to": "manager", "why": "评审否决论点"})]


def _outside_opinions(text: str) -> str:
    head, _, rest = text.partition("## 多空观点")
    return head + rest.partition("## 风控检查")[2]


# ---- 已交付 ----

def test_delivered_report_shows_ledger_values_not_claim_values() -> None:
    text = render(_delivered())
    assert "| close | 收盘价 | 100 USD | 2026-09-30 | 价格站上均线 |" in text
    assert "105" not in text                       # 模型把 100 转述成了 105，报告只显示账本的值
    assert "| ret_20 | 20日涨跌幅 | 4.25% |" in text
    assert "| rsi_14 | 14日相对强弱 | 61.3 |" in text


def test_delivered_report_states_rating_levels_and_confidence() -> None:
    text = render(_delivered())
    assert "评级：**Buy**；建议操作：**Buy**" in text
    assert "入场价：100.5；止损价：98.5；目标价：104.5（USD）" in text
    assert "仓位：5% 权益；预期持有：20 个交易日" in text
    assert "事实与规则：已核验" in text and "评审接受（2/3）" in text
    assert "上行趋势完好（依据：rsi_14）" in text and "收盘跌破 98.5" in text
    assert "- 保守评审：接受。止损 98.5 合理" in text
    assert "- 进取评审：反对（价位与仓位）。仓位过小" in text


def test_delivered_rule_table_prints_the_formula_and_computed_values() -> None:
    text = render(_delivered())
    assert "| 规则 | 公式 | 界限 | 计算值 | 结论 |" in text
    assert "| max_risk_per_trade_pct | (entry−stop)/entry×size_pct | 1 | 0.1 | 通过 |" in text
    assert "| cash_sufficient |  | — | — | 不适用 |" in text
    assert "在限内" not in text                     # 规则结果的说明文字不进报告


def test_a_table_cell_cannot_break_the_table() -> None:
    assert "动能偏强 ｜ 未到超买" in render(_delivered())


# ---- REVIEW ----

def test_review_report_has_no_rating_or_level() -> None:
    text = render(_review(), _rework_events())
    assert "**REVIEW：未给出评级。**原因：评审多数反对，未达成共识。" in text
    assert all(word not in _outside_opinions(text) for word in RATING_WORDS)
    assert all(level not in text for level in LEVELS)


def test_review_report_withholds_everything_that_describes_the_proposal() -> None:
    text = render(_review(), _rework_events())
    assert "上行趋势完好" not in text and "顺势做多" not in text      # 论点正文
    assert "止损 98.5 合理" not in text and "仓位过小" not in text    # 评审意见正文
    assert "止损 101.5" not in text and "单笔风险 1.4%" not in text   # 审计发现的说明文字
    assert "| 规则 | 界限 | 计算值 | 结论 |" in text                  # 没有公式一栏：公式里写着评级的名字
    assert "- 进取评审：反对（价位与仓位）。" in text                 # 谁反对、反对什么，仍然说
    assert "- [max_risk_per_trade_pct] proposal（修改人：交易员）：界限 1，实际 1.4" in text
    assert "- [stop_side] proposal.stop（修改人：交易员）\n" in text
    assert text.count("这部分内容不在报告里展示") == 2                # 论点一节与评审一节


def test_review_report_explains_an_intake_refusal_from_the_journal() -> None:
    snapshot = {"run/outcome": {"status": "review", "reason": "insufficient_history",
                                "facts_and_rules": "unverified", "judgment": "not reviewed"}}
    events = [Event(seq=1, ts="", type="run.started", actor="app", data={"request": {"ticker": "THIN"}}),
              Event(seq=2, ts="", type="gate.checked", actor="intake", data={
                  "gate": "intake", "findings": 1, "detail": [
                      {"check": "insufficient_history", "subject": "request",
                       "message": "已结算 K 线 40 根，不足下限 60 根，历史太短"}]})]
    text = render(snapshot, events)
    assert text.startswith("# THIN 分析报告（分析日 —）")
    assert "原因：历史数据不足。" in text
    assert "- 已结算 K 线 40 根，不足下限 60 根，历史太短" in text
    assert "事实与规则：未核验" in text and "模型判断：未经评审。" in text
    assert "（没有发生模型调用。）" in text


def test_not_reviewed_reasons_are_spelled_out() -> None:
    late = {**_delivered(), "run/outcome": {"status": "delivered", "reason": "budget_exhausted",
                                            "facts_and_rules": "verified",
                                            "judgment": "not reviewed: budget_exhausted"}}
    assert "模型判断：未经评审（token 预算已用尽）。" in render(late)
    off = {**_delivered(), "run/outcome": {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                                           "judgment": "not reviewed: judges disabled"}}
    assert "模型判断：未经评审（评审已关闭）。" in render(off)


# ---- 需要事件日志的部分 ----

def test_usage_totals_keep_input_and_output_apart() -> None:
    text = render(_delivered(), _started())
    assert "| technical | scripted | 1 | 1 | 1000 | 300 | 未定价 |" in text
    assert "| trader | priced-model | 1 | 2 | 2000 | 600 | 0.004 |" in text
    assert "| 合计 | — | 2 | 3 | 3000 | 900 | 0.004（另有未定价的调用） |" in text
    assert "token 合计 3900，上限 200000；成本合计 0.004（另有未定价的调用），上限 不设上限。" in text


def test_rework_section_lists_rejections_and_reruns_in_order() -> None:
    text = render(_delivered(), _rework_events())
    section = text.partition("## 返工记录")[2].partition("## 数据质量")[0]
    assert "以下是被退回的版本上的问题，不是结论。" in section
    assert "**第 1 次审计未通过**（事件 2）：" in section
    assert "- [stop_side] proposal.stop（修改人：交易员）：止损 101.5 不低于入场价 100.5，方向错误" in section
    assert "- 重跑：交易员及其后各段（审计发现 2 个问题）" in section
    assert "- 重跑：研究主管及其后各段（评审否决论点）" in section
    assert "第 2 次审计" not in section            # 通过的那一次不列
    assert section.index("交易员及其后各段") < section.index("研究主管及其后各段")


def test_rework_section_says_so_when_nothing_was_sent_back() -> None:
    assert "（无。审计一次通过，评审没有要求返工。）" in render(_delivered(), _started())


def test_run_info_comes_from_events() -> None:
    text = render(_delivered(), _started(), run_id="20260930-100000-ACME-2026-09-30-abcd")
    assert "- 运行 ID：20260930-100000-ACME-2026-09-30-abcd" in text
    assert "模式：脚本化模型（回复为预先编写）" in text
    assert "- 数据集：synthetic" in text
    assert "辩论 1 轮，审计 on，评审开" in text


def test_sections_that_need_events_say_so_without_them() -> None:
    assert render(_delivered()).count("（本节需要事件日志。）") == 2


def test_missing_artifacts_print_placeholders() -> None:
    snapshot = {k: v for k, v in _review().items()
                if k not in ("manager/thesis", "trader/proposal", "bull/argument", "bear/argument",
                             "technical/analysis", "audit/rules", "ledger/card")}
    text = render(snapshot)
    assert text.count("（无）") == 6


def test_timestamp_is_not_used() -> None:
    """报告渲染不读时钟：同一份事件，换个时间戳渲染出同样的文字。"""
    late = [e.model_copy(update={"ts": "2099-01-01T00:00:00Z"}) for e in _rework_events()]
    assert render(_delivered(), _rework_events()) == render(_delivered(), late)


# ---- 证据通道（BLUEPRINT 18.11）：三态、两个新节、标题只取条目列表 ----


def _channels_on(snapshot: dict) -> dict:
    """在快照里加上两条通道的键（数值来自 ACME 2026-09-30 的真实读取）。"""
    return {**snapshot,
            "fundamentals/status": {"channel": "fundamentals", "state": "available", "reason": ""},
            "fundamentals/facts": [
                {"id": "revenue_fy", "label": "营业收入（最近财年，截至 2025-12-31）", "value": 1081.0,
                 "unit": "USD million", "as_of": "2026-08-14", "source": "fundamentals.latest", "bars_used": 0}],
            "fundamentals/card": {"source": "合成数据", "currency": "USD", "fiscal_year_end": "2025-12-31",
                                  "filed": "2026-08-14", "rows": 21, "unavailable": (), "warnings": (
                                      "revenue 的 2025-12-31 财年被申报过 2 次（2026-02-19、2026-08-14）",)},
            "fundamentals/analysis": {"claims": (
                {"fact": "revenue_fy", "value": 1081.0, "reading": "重述后收入过十亿美元"},),
                "narrative": "增长稳健。"},
            "disclosures/status": {"channel": "disclosures", "state": "available", "reason": ""},
            "disclosures/items": [
                {"id": "D1", "published": "2026-09-22", "source": "合成数据", "kind": "股份回购",
                 "title": "董事会批准股份回购计划", "ref": "ACME-0011"}],
            "disclosures/card": {"source": "合成数据", "window_from": "2026-07-02", "window_to": "2026-09-30",
                                 "items": 1, "dropped": 1, "truncated": 0},
            "disclosures/analysis": {"signals": (
                {"item": "D1", "direction": "positive", "reading": "回购显示管理层信心"},),
                "stance": "positive", "confidence": "medium", "narrative": "窗口内一条公告。"}}


def test_a_run_without_channels_renders_no_channel_section() -> None:
    """不变式 17：通道没开时，报告不出现任何通道的节或数据质量行（与 2.0 相同）。"""
    text = render(_delivered(), _started_events := [])
    assert "基本面事实" not in text and "公告条目" not in text
    assert "基本面通道" not in text and "公告通道" not in text


def test_channel_sections_appear_when_the_keys_are_on_the_board() -> None:
    text = render(_channels_on(_delivered()))
    assert "## 基本面事实" in text and "## 公告条目（来源原文，未经核验）" in text
    assert "1081" in text and "USD million" in text
    assert "重述" in text                                     # 数据卡的重述提醒进报告


def test_item_titles_come_from_the_item_list_not_the_model() -> None:
    """不变式 3：标题只从 disclosures/items 取，模型的输出里即使有标题也不进表格。"""
    snapshot = _channels_on(_delivered())
    snapshot["disclosures/analysis"] = {"signals": (
        {"item": "D1", "direction": "positive", "reading": "模型编的另一个标题：完全不同的标题文字"},),
        "stance": "positive", "confidence": "low", "narrative": "叙述里也可以提到假标题：完全不同的标题文字"}
    text = render(snapshot)
    assert "董事会批准股份回购计划" in text
    assert text.count("完全不同的标题文字") == 1        # 只出现在 reading 里（narrative 不进报告），不出现在条目表


def test_an_unavailable_channel_states_so_in_quality_without_a_table() -> None:
    snapshot = {**_channels_on(_review()),
                "fundamentals/status": {"channel": "fundamentals", "state": "unavailable",
                                        "reason": "0000.HK 在宇宙 synthetic 里没有年报数据文件"}}
    snapshot.pop("fundamentals/facts")
    snapshot.pop("fundamentals/card")
    snapshot.pop("fundamentals/analysis")
    text = render(snapshot)
    assert "基本面通道：不可用（0000.HK 在宇宙 synthetic 里没有年报数据文件）" in text
    assert "## 基本面事实" not in text                  # 不可用的通道没有事实表


def test_an_empty_disclosures_window_uses_the_empty_wording() -> None:
    snapshot = {**_channels_on(_delivered()),
                "disclosures/status": {"channel": "disclosures", "state": "empty",
                                       "reason": "窗口 2026-04-01 到 2026-06-30 内没有公告"},
                "disclosures/items": []}
    snapshot.pop("disclosures/analysis")
    text = render(snapshot)
    assert "公告通道：窗口内没有公告" in text
    assert "（窗口内没有公告。）" in text
