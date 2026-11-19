"""运行页面的 Python 侧测试：事件完整嵌入、无外部资源、无注入调用、</script> 无法截断嵌入元素。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from insightcrew.app.replay_html import live_page, render, write
from insightcrew.kernel.events import Event

ASSET = live_page()


def _events() -> list[Event]:
    return [
        Event(seq=1, ts="2026-09-30T10:00:00Z", type="run.started", actor="app", data={
            "request": {"ticker": "ACME", "as_of": "2026-09-30"}, "version": "2.0.0",
            "mode": "scripted", "models": {"quick": "scripted", "deep": "scripted"},
            "output_mode": "auto", "flow": {}, "universe": "synthetic",
            "ceilings": {"run_tokens": 200000, "run_cost": None}, "limits": {}}),
        Event(seq=2, ts="", type="model.called", step="technical#1", actor="technical", data={
            "model": "scripted", "prompt": "<artifact key=\"ledger/facts\">…</artifact>",
            "messages": [], "output": {"claims": [], "narrative": "趋势向上"},
            "usage": {"requests": 1, "input_tokens": 1000, "output_tokens": 300}, "cost": None}),
        Event(seq=3, ts="", type="artifact.written", actor="technical", data={
            "key": "technical/analysis", "version": 1, "value": {"claims": [], "narrative": "趋势向上"}}),
        Event(seq=4, ts="", type="gate.checked", actor="audit", data={"gate": "audit", "findings": 1,
            "detail": [{"check": "max_risk_per_trade_pct", "subject": "proposal", "message": "单笔风险超限"}]}),
        Event(seq=5, ts="", type="route", actor="flow", data={"to": "trader", "why": "审计发现 1 个问题"}),
        Event(seq=6, ts="", type="run.finished", actor="flow", data={"status": "delivered", "reason": "ok"}),
    ]


def _embedded(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="embedded">(.*?)</script>', html, re.S)
    assert match, "页面缺少嵌入元素"
    return json.loads(match.group(1))


def test_static_page_holds_every_event() -> None:
    events = _events()
    html = render(events)
    payload = _embedded(html)
    assert len(payload["events"]) == len(events)
    assert payload["events"][0]["type"] == "run.started"
    assert payload["events"][-1]["type"] == "run.finished"
    assert payload["bars"] is None


def test_static_page_carries_bars_when_given() -> None:
    payload = _embedded(render(_events(), bars=[{"date": "2026-09-29", "close": 173.24}]))
    assert payload["bars"] == [{"date": "2026-09-29", "close": 173.24}]


def test_asset_has_no_external_resources() -> None:
    assert not re.search(r'(src|href)\s*=\s*"(?!#)', ASSET), "页面不得引用外部资源"
    assert "https://" not in ASSET and "http://" not in ASSET.replace("http://www.w3.org", "")
    # SVG 命名空间是 W3C 的固定标识符，不是网络请求；其余 http(s) 引用一律不允许。


def test_asset_has_no_markup_injecting_call() -> None:
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "eval(", "new Function"):
        assert forbidden not in ASSET, f"页面资产不得使用 {forbidden}"


def test_reply_containing_script_close_cannot_break_the_embedded_element() -> None:
    events = _events()
    evil = dict(events[1].data)
    evil["output"] = {"claims": [], "narrative": "</script><script>alert(1)</script>"}
    events[1] = events[1].model_copy(update={"data": evil})
    html = render(events)
    payload = _embedded(html)                      # 能整体解析：元素没有被提前结束
    assert payload["events"][1]["data"]["output"]["narrative"] == "</script><script>alert(1)</script>"
    assert html.count("</script>") == 2            # 嵌入元素与页面脚本各一个，模型文本不算


def test_embedded_element_is_declared_in_live_mode() -> None:
    assert '<script type="application/json" id="embedded">null</script>' in ASSET


def test_write_creates_replay_html(tmp_path: Path) -> None:
    path = write(tmp_path, _events())
    assert path == tmp_path / "replay.html"
    assert _embedded(path.read_text(encoding="utf-8"))["events"]


def test_render_is_deterministic() -> None:
    assert render(_events()) == render(_events())


def test_csp_is_declared() -> None:
    assert 'http-equiv="Content-Security-Policy"' in ASSET
    assert "default-src 'none'" in ASSET
    assert "connect-src 'self'" in ASSET


# ---- 证据通道（BLUEPRINT 18.11）：流水线两段、证据区两块、没开通道的运行一个都不出现 ----


def _channel_events() -> list[Event]:
    """在最小事件序列上补一次带通道的运行会写出的黑板键。"""
    extra = [
        Event(seq=7, ts="", type="artifact.written", actor="evidence", data={
            "key": "fundamentals/status", "version": 1,
            "value": {"channel": "fundamentals", "state": "available", "reason": ""}}),
        Event(seq=8, ts="", type="artifact.written", actor="evidence", data={
            "key": "fundamentals/facts", "version": 1,
            "value": [{"id": "revenue_fy", "label": "营业收入（最近财年，截至 2025-12-31）",
                       "value": 1081.0, "unit": "USD million", "as_of": "2026-08-14",
                       "source": "fundamentals.latest", "bars_used": 0}]}),
        Event(seq=9, ts="", type="artifact.written", actor="disclosures", data={
            "key": "disclosures/items", "version": 1,
            "value": [{"id": "D1", "published": "2026-09-22", "source": "合成数据",
                       "kind": "股份回购", "title": "董事会批准股份回购计划", "ref": "ACME-0011"}]}),
    ]
    return _events() + extra


def test_the_page_declares_the_channel_stages_and_sections() -> None:
    assert "基本面分析" in ASSET and "公告分析" in ASSET          # 流水线的两段
    assert "基本面分析师" in ASSET and "公告分析师" in ASSET      # 时间线的泳道
    assert "fundamentals/status" in ASSET and "disclosures/items" in ASSET
    assert "公告条目（来源原文，未经核验）" in ASSET and "基本面事实" in ASSET
    assert "CHANNEL_STAGES" in ASSET                              # 没开的通道不加进流水线


def test_channel_events_are_embedded() -> None:
    payload = _embedded(render(_channel_events()))
    keys = [e["data"]["key"] for e in payload["events"] if e["type"] == "artifact.written"]
    assert "fundamentals/facts" in keys and "disclosures/items" in keys


def test_a_channelless_run_embeds_no_channel_keys() -> None:
    payload = _embedded(render(_events()))
    keys = [e["data"]["key"] for e in payload["events"] if e["type"] == "artifact.written"]
    assert not any(key.startswith(("fundamentals/", "disclosures/")) for key in keys)


# ---- 版本 2.2（BLUEPRINT 19.15）：新的流水线段、并行组、泳道、表单与证据区 ----


def _v22_events() -> list[Event]:
    """在最小事件序列上补 v2.2 各能力会写出的黑板键（形状与契约一致的最小字典）。"""
    extra = [
        Event(seq=7, ts="", type="artifact.written", actor="portfolio", data={
            "key": "portfolio/state", "version": 1,
            "value": {"state": "held", "currency": "USD", "quantity": 90.0,
                      "position_pct": 90.0, "cash_pct": 10.0, "reason": ""}}),
        Event(seq=8, ts="", type="artifact.written", actor="evidence", data={
            "key": "news/status", "version": 1,
            "value": {"channel": "news", "state": "available", "reason": ""}}),
        Event(seq=9, ts="", type="artifact.written", actor="evidence", data={
            "key": "news/items", "version": 1,
            "value": [{"id": "N1", "published": "2026-09-28", "source": "合成数据",
                       "title": "Acme 拿下大额机器人订单", "url": "https://example.com/n1"}]}),
        Event(seq=10, ts="", type="artifact.written", actor="sentiment", data={
            "key": "sentiment/posts", "version": 1,
            "value": [{"id": "S1", "published": "2026-09-29", "source": "合成数据",
                       "text": "机器人板块要起飞", "tag": "bullish"}]}),
        Event(seq=11, ts="", type="model.called", step="risk.aggressive#1", actor="risk.aggressive", data={
            "model": "scripted", "prompt": "…", "messages": [],
            "output": {"stance": "aggressive", "critique": "仓位太小", "size_pct": None,
                       "stop": None, "target": None, "cites": [], "rebuttal": ""},
            "usage": {"requests": 1, "input_tokens": 900, "output_tokens": 200}, "cost": None}),
        Event(seq=12, ts="", type="artifact.written", actor="risk.aggressive", data={
            "key": "risk/aggressive", "version": 1,
            "value": {"stance": "aggressive", "critique": "仓位太小", "size_pct": None,
                      "stop": None, "target": None, "cites": [], "rebuttal": ""}}),
        Event(seq=13, ts="", type="artifact.written", actor="technical", data={
            "key": "tools/facts", "version": 1,
            "value": [{"id": "sma_30", "value": 99.25, "unit": "USD", "as_of": "2026-09-30"}]}),
    ]
    return _events() + extra


def test_the_page_declares_the_v22_stages_and_actors() -> None:
    assert "新闻分析" in ASSET and "情感分析" in ASSET and "风险辩论" in ASSET   # 流水线的三段
    assert "新闻分析师" in ASSET and "情感分析师" in ASSET                      # 时间线泳道
    assert "激进风控" in ASSET and "保守风控" in ASSET and "中立风控" in ASSET
    assert '"risk.aggressive": "risk"' in ASSET                                 # ACTOR_STAGE 映射
    assert '"risk.neutral": "risk"' in ASSET and '"risk.conservative": "risk"' in ASSET
    assert 'news: "news/status"' in ASSET and 'sentiment: "sentiment/status"' in ASSET
    assert 'risk: "risk/aggressive"' in ASSET                                   # 段何时出现
    assert "KEY_STAGE" in ASSET                                                  # 产物键前缀归段


def test_the_page_declares_the_v22_sections_and_the_parallel_group() -> None:
    for heading in ("新闻标题（来源原文，未经核验）", "预测市场（来源原话，未经核验）", "社交情感",
                    "用到的课程", "风险三方意见（未经审计的意见）", "组合状态", "宏观事实"):
        assert heading in ASSET, heading
    assert "ANALYST_KEYS" in ASSET and "并行" in ASSET       # 并行模式：分析师并列，不画箭头
    assert "run/wall_ms" in ASSET                            # 墙钟：黑板键在才画（问题 20 的 C 案）


def test_the_form_declares_the_v22_fields() -> None:
    for field in ("f-risk-rounds", "f-tools", "f-memory", "f-portfolio", "f-channels"):
        assert f'id="{field}"' in ASSET, field


def test_v22_events_are_embedded() -> None:
    payload = _embedded(render(_v22_events()))
    keys = [e["data"]["key"] for e in payload["events"] if e["type"] == "artifact.written"]
    for key in ("portfolio/state", "news/status", "news/items", "sentiment/posts",
                "risk/aggressive", "tools/facts"):
        assert key in keys
    actors = {e["actor"] for e in payload["events"]}
    assert "risk.aggressive" in actors
