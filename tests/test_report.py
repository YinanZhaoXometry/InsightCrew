"""报告渲染的测试：REVIEW 报告里没有评级与价位；事实数值取自账本，而不是模型的转述。"""
from __future__ import annotations

from insightcrew.app.report import render
from insightcrew.kernel.events import Event

RATING_WORDS = ("Buy", "Sell", "Overweight", "Underweight")


def _facts() -> list[dict]:
    return [
        {"id": "close", "label": "收盘价", "value": 100.0, "unit": "USD", "as_of": "2026-09-30",
         "source": "indicators.last", "bars_used": 300},
        {"id": "atr_14", "label": "14日平均真实波幅", "value": 2.0, "unit": "USD", "as_of": "2026-09-30",
         "source": "indicators.atr", "bars_used": 15},
        {"id": "rsi_14", "label": "14日相对强弱", "value": 61.3, "unit": "", "as_of": "2026-09-30",
         "source": "indicators.rsi", "bars_used": 15},
    ]


def _delivered_snapshot() -> dict:
    return {
        "request/scope": {"ticker": "ACME", "as_of": "2026-09-30", "universe": "synthetic", "requested": "ACME"},
        "ledger/instrument": {"ticker": "ACME", "name": "Acme Robotics（合成数据）", "market": "US",
                              "currency": "USD", "benchmark": "SYNUS", "lot": 1, "synthetic": True},
        "ledger/facts": _facts(),
        "ledger/card": {"first_bar": "2025-01-02", "last_bar": "2026-09-30", "bars": 440, "stale_days": 0,
                        "dropped_rows": 0, "gaps": (), "unavailable": (), "warnings": ()},
        "rules/limits": [{"key": "max_risk_per_trade_pct", "limit": 1.0, "formula": "(entry−stop)/entry×size_pct",
                          "desc": "单笔风险", "source": "风控规则 2.1"}],
        "technical/analysis": {"claims": (
            {"fact": "close", "value": 105.0, "reading": "价格站上均线"},          # 转述错了，报告必须显示账本的 100
            {"fact": "rsi_14", "value": 61.3, "reading": "动能偏强"}),
            "narrative": "趋势向上。"},
        "bull/argument": {"points": ({"text": "动能强", "cites": ("rsi_14",)},), "rebuttal": ""},
        "bear/argument": {"points": ({"text": "波动大", "cites": ("atr_14",)},), "rebuttal": ""},
        "manager/thesis": {"rating": "Buy", "horizon_days": 20,
                           "points": ({"text": "趋势向上", "cites": ("rsi_14",)},),
                           "invalidation": ("跌破止损",), "summary": "做多"},
        "trader/proposal": {"action": "Buy", "entry": 100.5, "stop": 98.5, "target": 104.5, "size_pct": 5.0,
                            "horizon_days": 20, "cites": ("atr_14",), "rationale": "止损一个 ATR"},
        "audit/rules": [
            {"key": "max_risk_per_trade_pct", "status": "pass", "limit": 1.0, "actual": 0.1, "message": "在限内"},
            {"key": "min_reward_risk", "status": "pass", "limit": 1.5, "actual": 2.0, "message": "在限内"},
            {"key": "cash_sufficient", "status": "n/a", "limit": None, "actual": None, "message": "未给出现金"},
        ],
        "judge.evidence/verdict": {"accept": True, "target": "none", "critique": "证据充分", "cites": ("rsi_14",)},
        "judge.conservative/verdict": {"accept": True, "target": "none", "critique": "止损合理", "cites": ()},
        "judge.aggressive/verdict": {"accept": False, "target": "levels", "critique": "仓位过小", "cites": ()},
        "judges/status": "accepted 2/3",
        "run/outcome": {"status": "delivered", "reason": "ok", "facts_and_rules": "verified",
                        "judgment": "accepted 2/3"},
        "run/usage": [{"role": "technical", "model": "scripted", "calls": 1, "requests": 1,
                       "input_tokens": 1000, "output_tokens": 300, "cost": None},
                      {"role": "trader", "model": "scripted", "calls": 1, "requests": 2,
                       "input_tokens": 2000, "output_tokens": 600, "cost": 0.004}],
    }


def _review_snapshot() -> dict:
    snapshot = _delivered_snapshot()
    return {**snapshot,
            "run/outcome": {"status": "review", "reason": "contested", "facts_and_rules": "verified",
                            "judgment": "contested"}}


def _started() -> list[Event]:
    return [Event(seq=1, ts="2026-09-30T10:00:00Z", type="run.started", actor="app", data={
        "request": {"ticker": "ACME", "as_of": "2026-09-30"},
        "version": "2.0.0", "mode": "scripted", "models": {"quick": "scripted", "deep": "scripted"},
        "output_mode": "auto", "flow": {"debate_rounds": 1, "audit": "on", "judges": True,
                                        "audit_rounds": 3, "judge_rounds": 2, "quorum": 2},
        "universe": "synthetic", "ceilings": {"run_tokens": 200000, "run_cost": None}, "limits": {}})]


def _rework_events() -> list[Event]:
    return [*_started(),
            Event(seq=2, ts="", type="artifact.written", actor="auditor", data={
                "key": "audit/findings", "version": 1, "value": [
                    {"check": "max_risk_per_trade_pct", "layer": "L3", "owner": "trader", "subject": "proposal",
                     "message": "单笔风险超限", "limit": 1.0, "actual": 1.4}]}),
            Event(seq=3, ts="", type="route", actor="flow", data={"to": "trader", "why": "审计发现 1 个问题"}),
            Event(seq=4, ts="", type="artifact.written", actor="auditor", data={
                "key": "audit/findings", "version": 2, "value": []})]


def test_delivered_report_shows_ledger_values_not_claim_values() -> None:
    text = render(_delivered_snapshot())
    assert "| close" in text and "100" in text
    assert "105" not in text                       # 模型转述错了 105，报告只显示账本的 100
    assert "Buy" in text and "100.5" in text
    assert "accepted 2/3" in text or "评审通过" in text
    assert "max_risk_per_trade_pct" in text and "0.1" in text


def test_review_report_has_no_rating_or_level() -> None:
    text = render(_review_snapshot())
    assert "REVIEW：未给出评级" in text
    assert "评审多数反对" in text
    # 去掉观点一节后，评级字样不得出现
    head, _, rest = text.partition("## 多空观点")
    _, _, tail = rest.partition("## 风控检查")
    assert all(word not in head + tail for word in RATING_WORDS)
    assert "100.5" not in text                     # 入场价不出现
    assert "98.5" not in text                      # 止损价不出现


def test_review_report_without_thesis_prints_placeholders() -> None:
    snapshot = {k: v for k, v in _review_snapshot().items()
                if k not in ("manager/thesis", "trader/proposal", "bull/argument", "bear/argument")}
    text = render(snapshot)
    assert "REVIEW：未给出评级" in text
    assert "（无）" in text


def test_usage_totals_and_unpriced_models() -> None:
    text = render(_delivered_snapshot(), _started())
    assert "未定价" in text                        # scripted 模型查不到价格
    assert "3900" in text                          # token 合计：输入 1000+2000，输出 300+600
    assert "0.004" in text


def test_rework_section_comes_from_events() -> None:
    text = render(_delivered_snapshot(), _rework_events())
    assert "第 1 版被退回" in text
    assert "max_risk_per_trade_pct" in text and "单笔风险超限" in text
    assert "重跑：交易员（审计发现 1 个问题）" in text


def test_run_info_comes_from_events() -> None:
    text = render(_delivered_snapshot(), _started(), run_id="20260930-100000-ACME-2026-09-30-abcd")
    assert "20260930-100000-ACME-2026-09-30-abcd" in text
    assert "脚本化模型" in text
    assert "synthetic" in text


def test_events_missing_leaves_those_sections_minimal() -> None:
    text = render(_delivered_snapshot())
    assert "本节需要事件日志" in text


def test_timestamp_is_not_used() -> None:
    """报告渲染不读时钟：同一份事件，换个时间戳渲染出同样的文字。"""
    events = _started()
    late = [e.model_copy(update={"ts": "2099-01-01T00:00:00Z"}) for e in events]
    assert render(_delivered_snapshot(), events) == render(_delivered_snapshot(), late)
