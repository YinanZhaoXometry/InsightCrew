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
