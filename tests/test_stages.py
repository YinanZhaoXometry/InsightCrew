"""真实代码阶段的测试：每次准入拒绝都不产生模型调用；两条路径的分歧让运行停止；
闸门每次检查都在日志里留下一条带明细的事件，拦下运行时也一样。"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from insightcrew.adapters.declarations import load_indicators, load_rules
from insightcrew.adapters.market_csv import CsvMarket
from insightcrew.adapters.scripted import Script
from insightcrew.domain.types import (
    Analysis,
    AnalysisSeq,
    ChannelStatus,
    DisclosureAnalysis,
    Fact,
    Finding,
    Item,
    Proposal,
    Thesis,
)
from insightcrew.flows import stages
from insightcrew.flows.deps import Deps
from insightcrew.kernel.board import Board
from insightcrew.kernel.budget import Budget
from insightcrew.kernel.events import Journal
from insightcrew.kernel.runtime import Halt, Run

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)


def _deps(today: date = AS_OF) -> Deps:
    """真实适配器与真实声明；离线剧本没有任何回答——任何一次模型调用都会失败。"""
    return Deps(
        market=CsvMarket(ROOT / "data" / "market", "synthetic"),
        indicators=load_indicators(ROOT / "data" / "indicators.toml"),
        rules=load_rules(ROOT / "data" / "rules.toml"),
        today=today,
        universe="synthetic",
    )


def _run(deps: Deps) -> Run:
    journal = Journal()
    return Run(id="test", journal=journal, board=Board(journal), models=Script({}), budget=Budget())


def _no_model_call(run: Run) -> None:
    assert [e for e in run.journal.events if e.type == "model.called"] == []


def _refused(ticker: str, as_of: str = "") -> tuple[Run, Halt]:
    """跑一次准入并捕获停机原因，返回同一个运行供断言使用。"""
    deps = _deps()
    run = _run(deps)
    request: dict = {"ticker": ticker}
    if as_of:
        request["as_of"] = as_of
    with pytest.raises(Halt) as halt:
        stages.intake(run, request, deps)
    return run, halt.value


def test_intake_accepts_acme_without_model_call() -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    scope = run.board.get("request/scope")
    assert scope["ticker"] == "ACME" and scope["as_of"] == "2026-09-30" and scope["requested"] == "ACME"
    assert _gates(run) == [{"gate": "intake", "findings": 0, "detail": []}]
    _no_model_call(run)


def _gates(run: Run) -> list[dict]:
    return [e.data for e in run.journal.events if e.type == "gate.checked"]


def test_intake_thin_is_refused() -> None:
    run, halt = _refused("THIN", "2026-09-30")
    assert halt.reason == "insufficient_history"
    assert not run.board.has("request/scope")
    _no_model_call(run)


def test_refusal_is_recorded_in_the_journal_with_its_explanation() -> None:
    """拦下运行的闸门先记一条事件再停：原因的细节在日志里，不只是一个代号。"""
    run, halt = _refused("THIN", "2026-09-30")
    (gate,) = _gates(run)
    assert gate["gate"] == "intake" and gate["findings"] == 1
    assert gate["detail"] == [{"check": "insufficient_history", "subject": "request", "message": halt.detail}]
    assert "60" in halt.detail                      # 说明里有规则的下限


def test_intake_stale_is_refused() -> None:
    run, halt = _refused("STALE", "2026-09-30")
    assert halt.reason == "stale_data"
    _no_model_call(run)


def test_intake_unknown_and_invalid_tickers() -> None:
    run, halt = _refused("NOPE")
    assert halt.reason == "unknown_ticker"
    _no_model_call(run)
    run, halt = _refused("BRK.B")
    assert halt.reason == "invalid_ticker"
    _no_model_call(run)


def test_intake_future_date() -> None:
    run, halt = _refused("ACME", "2027-01-01")
    assert halt.reason == "future_date"
    _no_model_call(run)


def test_intake_default_date_is_last_settled_bar() -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME"}, deps)
    assert run.board.get("request/scope")["as_of"] == "2026-09-30"


def test_ledger_builds_facts_and_card() -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    stages.ledger(run, deps)
    facts = run.board.get("ledger/facts")
    assert len(facts) == 29
    assert {f["id"] for f in facts} >= {"close", "rsi_14", "atr_14", "macd_hist"}
    assert all(f["as_of"] <= "2026-09-30" for f in facts)
    assert run.board.get("ledger/card")["dropped_rows"] == 0
    keys = {s["key"] for s in run.board.get("rules/limits")}
    assert "data_fresh" not in keys and "min_history" not in keys
    assert "max_risk_per_trade_pct" in keys
    _no_model_call(run)


def test_ledger_on_gappy_counts_dropped_rows() -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "GAPPY", "as_of": "2026-09-30"}, deps)
    stages.ledger(run, deps)
    assert run.board.get("ledger/card")["dropped_rows"] == 2


def test_planted_disagreement_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    real = stages.recompute_ledger

    def wrong(*args: object, **kwargs: object) -> dict:
        """把路径 2 的每个数都抬高 1.0，制造必然被发现的分歧。"""
        call_args: tuple = args                      # real() 的参数有类型；object 只是为了挡住误用
        return {k: (v + 1.0 if v is not None else None) for k, v in real(*call_args, **kwargs).items()}

    monkeypatch.setattr(stages, "recompute_ledger", wrong)
    with pytest.raises(Halt) as halt:
        stages.ledger(run, deps)
    assert halt.value.reason == "ledger_inconsistent"
    assert not run.board.has("ledger/facts")        # 不一致的账本不上黑板
    gate = _gates(run)[-1]
    assert gate["gate"] == "ledger" and gate["findings"] == 29
    assert {item["check"] for item in gate["detail"]} == {"ledger_inconsistent"}
    assert "rsi_14" in {item["subject"] for item in gate["detail"]}


def _fill_pipeline(run: Run, deps: Deps) -> None:
    """准入、账本之后，写入互相一致的分析、论点与方案（数值取自账本，保证审计通过）。"""
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    stages.ledger(run, deps)
    facts = {f["id"]: f for f in run.board.get("ledger/facts")}
    close, atr = facts["close"]["value"], facts["atr_14"]["value"]
    claims = [{"fact": fid, "value": facts[fid]["value"], "reading": "照抄账本，没有改动"}
              for fid in ("close", "atr_14", "rsi_14")]
    run.board.put("technical/analysis", Analysis.model_validate(
        {"claims": claims, "narrative": "账本数值原样引用，三件事都说了。"}), by="technical")
    run.board.put("manager/thesis", Thesis.model_validate({
        "rating": "Buy", "horizon_days": 20,
        "points": [{"text": "账本支持", "cites": ["close", "rsi_14"]}],
        "invalidation": ["跌破止损"], "summary": "做多"}), by="manager")
    entry, stop = close, close - 1.05 * atr
    run.board.put("trader/proposal", Proposal.model_validate({
        "action": "Buy", "entry": entry, "stop": stop, "target": entry + 2.1 * (entry - stop),
        "size_pct": 5.0, "horizon_days": 20, "cites": ["close", "atr_14"],
        "rationale": "止损一个 ATR 略多，风险在限内"}), by="trader")


def test_audit_passes_a_consistent_case() -> None:
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    findings = stages.audit(run, deps)
    assert findings == []
    assert run.board.get("audit/status")["verdict"] == "pass"
    assert all(r["status"] in ("pass", "n/a") for r in run.board.get("audit/rules"))
    assert _gates(run)[-1] == {"gate": "audit", "findings": 0, "detail": []}


def test_audit_gate_event_carries_each_finding() -> None:
    """审计的闸门事件带着每条发现：运行页面点开审计就能看到是什么问题、该谁改。"""
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    proposal = run.board.get("trader/proposal", Proposal)
    run.board.put("trader/proposal", proposal.model_copy(update={"size_pct": 60.0}), by="trader")
    findings = stages.audit(run, deps)
    gate = _gates(run)[-1]
    assert gate["findings"] == len(findings) > 0
    assert [(item["check"], item["subject"], item["owner"]) for item in gate["detail"]] == [
        (f.check, f.subject, f.owner) for f in findings]
    assert "max_risk_per_trade_pct" in {item["check"] for item in gate["detail"]}
    assert run.board.get("audit/status")["verdict"] == "revise"


def test_audited_current_follows_the_versions() -> None:
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    assert not stages.audited_current(run)          # 还没审计
    stages.audit(run, deps)
    assert stages.audited_current(run)              # 当前版本就是通过审计的那一版
    run.board.put("trader/proposal", run.board.get("trader/proposal"), by="trader")
    assert not stages.audited_current(run)          # 方案有了新版本，审计过期


def test_report_stage_writes_files(tmp_path: Path) -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    stages.ledger(run, deps)
    rendered = "# 报告\n"
    stage_deps = dataclasses.replace(deps, out_dir=tmp_path, render=lambda _snap: rendered)
    stages.report(run, stage_deps)
    assert (tmp_path / "board.json").read_text(encoding="utf-8").startswith("{")
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == rendered


def test_report_stage_without_out_dir_does_nothing() -> None:
    deps = _deps()
    run = _run(deps)
    stages.report(run, deps)                        # out_dir 与 render 都是 None：什么都不发生


# ---- 证据通道（BLUEPRINT 18.8）。WP13：evidence 是存根，被点名一律不可用 ----


def test_evidence_stub_marks_named_channels_unavailable() -> None:
    deps = _deps()
    run = _run(deps)
    stages.intake(run, {"ticker": "ACME", "as_of": "2026-09-30"}, deps)
    stages.ledger(run, deps)
    stages.evidence(run, deps, ("fundamentals", "disclosures"))
    for channel in ("fundamentals", "disclosures"):
        status = run.board.get(f"{channel}/status", ChannelStatus)
        assert status.state == "unavailable" and status.reason
        assert not stages.channel_ready(run, channel)
        assert not run.board.has(f"{channel}/facts") and not run.board.has(f"{channel}/items")
    assert _gates(run)[-1]["gate"] == "evidence"
    _no_model_call(run)


def test_channel_ready_is_false_without_a_status() -> None:
    run = _run(_deps())
    assert not stages.channel_ready(run, "fundamentals")    # 通道没被点名：连状态键都没有


def test_audit_without_channel_analysis_writes_no_channel_keys() -> None:
    """通道分析不在板上：审计写出的键与版本 2.0 相同（兼容规则 18.3）。"""
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    assert stages.audit(run, deps) == []
    for key in ("audit/fundamentals", "audit/disclosures", "audit/channels",
                "fundamentals/status", "disclosures/status"):
        assert not run.board.has(key)


def _fundamentals_on_board(run: Run, misquote: bool = False) -> None:
    """放上一套互相一致的基本面事实与读数；misquote 时把第一条 claim 的数值放大 3%。"""
    def fact(fid: str, value: float) -> Fact:
        return Fact(id=fid, label=fid, value=value, unit="USD million",
                    as_of="2026-02-19", source="fundamentals.latest", bars_used=0)

    facts = [fact("revenue_fy", 1105.0), fact("net_income_fy", 97.0), fact("net_margin_pct", 8.78)]
    claims = [{"fact": f.id, "value": f.value * (1.03 if misquote and i == 0 else 1.0),
               "reading": "照抄事实"} for i, f in enumerate(facts)]
    run.board.put("fundamentals/facts", facts, by="fundamentals")
    run.board.put("fundamentals/analysis", Analysis.model_validate(
        {"claims": claims, "narrative": "三条都说了。"}), by="fundamentals")


def test_audit_checks_fundamentals_when_its_analysis_is_on_the_board() -> None:
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    _fundamentals_on_board(run, misquote=True)
    findings = stages.audit(run, deps)
    bad = [f for f in findings if f.owner == "fundamentals"]
    assert [(f.check, f.subject) for f in bad] == [
        ("claim_value_matches_fact", "fundamentals.claims[0]")]
    assert run.board.get("audit/fundamentals", list[Finding]) == bad
    channels = run.board.get("audit/channels", list[AnalysisSeq])
    assert [(c.key, c.seq) for c in channels] == [("fundamentals/analysis",
                                                   run.board.seq("fundamentals/analysis"))]
    assert not run.board.has("audit/disclosures")            # 公告分析不在板上：不写


def _disclosures_on_board(run: Run, ghost: bool = False) -> None:
    items = [Item(id="D1", published="2026-09-22", source="合成数据", kind="股份回购",
                  title="董事会批准股份回购计划", ref="ACME-0011")]
    run.board.put("disclosures/items", items, by="disclosures")
    signals = [{"item": "D99" if ghost else "D1", "direction": "positive", "reading": "回购通常偏正面"}]
    run.board.put("disclosures/analysis", DisclosureAnalysis.model_validate(
        {"signals": signals, "stance": "positive", "confidence": "medium", "narrative": "窗口内一条公告。"}),
        by="disclosures")


def test_audit_checks_disclosure_signals_against_the_item_list() -> None:
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    _disclosures_on_board(run, ghost=True)
    findings = stages.audit(run, deps)
    bad = [f for f in findings if f.owner == "disclosures"]
    assert [(f.check, f.subject) for f in bad] == [
        ("signal_cites_known_item", "disclosures.signals[0]")]
    assert run.board.get("audit/disclosures", list[Finding]) == bad
    assert [c.key for c in run.board.get("audit/channels", list[AnalysisSeq])] == [
        "disclosures/analysis"]


def test_audited_current_tracks_channel_analysis_versions() -> None:
    deps = _deps()
    run = _run(deps)
    _fill_pipeline(run, deps)
    _fundamentals_on_board(run)
    stages.audit(run, deps)
    assert stages.audited_current(run)                       # 通道分析也是通过审计的那一版
    _fundamentals_on_board(run)                              # 重写一版：序号变了
    assert not stages.audited_current(run)                   # 审计过期，方案必须重新过审计
