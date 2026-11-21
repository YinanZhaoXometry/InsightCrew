"""运行页面的 Python 侧测试：事件完整嵌入、无外部资源、无注入调用、</script> 无法截断嵌入元素。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from insightcrew.app import replay_html
from insightcrew.app.replay_html import live_page, render, write
from insightcrew.kernel.events import Event

ASSET = live_page()
ASSETS = Path(replay_html.__file__).resolve().parent / "assets"


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


# ---- 版本 2.3（BLUEPRINT 20.9）：源文件拆分、拼装、页签外壳与轨迹嵌入 ----


def test_the_source_layout_is_the_one_of_20_9() -> None:
    assert sorted(path.name for path in (ASSETS / "js").glob("*.js")) == [
        "00-core.js", "05-labels.js", "10-model.js", "20-shell.js", "25-activity.js",
        "26-screens.js", "30-verdict.js", "35-process.js", "40-evidence.js", "45-audit.js",
        "50-usage.js", "55-launch.js", "60-events.js", "90-boot.js"]
    assert sorted(path.name for path in (ASSETS / "css").glob("*.css")) == [
        "00-tokens.css", "10-base.css", "15-screens.css", "30-verdict.css", "35-process.css",
        "40-evidence.css", "50-usage.css", "55-launch.css"]
    assert (ASSETS / "page.html").is_file()


def test_the_page_assembles_every_module_in_file_order() -> None:
    page = live_page()
    last = -1
    for folder, suffix in (("css", ".css"), ("js", ".js")):      # 样式在前，脚本在后，各自按文件名序
        for path in sorted((ASSETS / folder).glob("*" + suffix)):
            at = page.index(path.read_text(encoding="utf-8").strip())
            assert at > last, f"{path.name} 不在拼装顺序里"
            last = at


def test_the_shell_declares_the_tabs_and_empty_views_say_who_fills_them() -> None:
    for tab in ("过程", "证据", "规则与审计", "用量与耗时", "原始事件"):
        assert tab in ASSET, tab
    for view in ("view-process", "view-evidence", "view-audit", "view-usage", "view-events"):
        assert f'id="{view}"' in ASSET, view
    assert "fold" in ASSET and "registerView" in ASSET           # 唯一的模型与视图注册表
    assert "WP47" in ASSET and "WP48" in ASSET                   # 空页签写明由哪个包填充
    assert "run-state" in ASSET                                  # 页头的运行状态


def test_render_embeds_the_trace_beside_events_and_bars() -> None:
    trace: list[dict[str, object]] = [{"ts": "2026-11-20T00:00:01.000+00:00", "kind": "role.started",
              "step": "technical#1", "role": "technical", "model": "scripted"}]
    payload = _embedded(render(_events(), bars=[{"date": "2026-09-29", "close": 173.24}], trace=trace))
    assert payload["trace"] == trace
    assert payload["bars"] == [{"date": "2026-09-29", "close": 173.24}]


def test_render_without_a_trace_embeds_null() -> None:
    assert _embedded(render(_events()))["trace"] is None


def test_live_page_is_deterministic() -> None:
    assert live_page() == live_page()


def test_the_assembler_refuses_a_shell_without_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "page.html"
    bad.write_text("<html><style></style><script>(() => {})();</script></html>", encoding="utf-8")
    monkeypatch.setattr(replay_html, "_PAGE", bad)
    with pytest.raises(RuntimeError):
        live_page()


def test_the_assembler_refuses_a_script_that_closes_the_tag(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    assets = tmp_path / "assets"
    shutil.copytree(ASSETS, assets)
    bad_js = assets / "js" / "99-bad.js"
    bad_js.write_text("const bad = 1; // " + chr(60) + "/script>\n", encoding="utf-8")
    monkeypatch.setattr(replay_html, "_ASSETS", assets)
    with pytest.raises(RuntimeError):
        live_page()


def test_the_page_declares_the_three_screens_of_wp54() -> None:
    """WP54：空状态 / 新建分析 / 工作台三屏；两步预估；历史行卡；回放条上提。"""
    for screen in ("screen-empty", "screen-setup", "run"):
        assert f'id="{screen}"' in ASSET, screen
    assert "每一个交易结论，都能回到证据。" in ASSET                     # 空屏的大标题（原型文案）
    assert 'id="btn-new"' in ASSET and 'id="btn-back-work"' in ASSET     # 页头新建键与返回键
    assert "btn-estimate" in ASSET and "查看花费预估" in ASSET            # 两步预估的第一步
    assert "确认预估并开始" in ASSET and "配置已变更，请重新预估" in ASSET  # 第二步与作废标记
    assert 'id="active-note"' in ASSET                                   # setup 屏的活跃运行提示
    assert 'id="playbar"' in ASSET and 'id="replay-marks"' in ASSET      # 回放条在工作台顶部
    assert "过去的运行" in ASSET and 'id="history-body"' in ASSET        # 历史是屏外常驻一节
    assert "marksFor" in ASSET and "showScreen" in ASSET                 # 26-screens 的两个入口


def test_the_page_declares_the_quality_layer_of_wp51() -> None:
    """20.10 质量清单落在页面上的静态部分：活区、页签键盘操作、抽屉对话框、断流说明。"""
    page = ASSET
    assert 'id="banner" role="alert"' in page                      # 失败横幅是警报活区
    assert 'id="sr-status" role="status" aria-live="polite"' in page   # 运行状态的播报活区
    assert 'id="sr-activity" role="status" aria-live="polite"' in page  # 活动区的播报活区
    assert 'id="stream-note" role="status" hidden' in page         # 断流重连的说明（活区）
    assert 'role="tablist"' in page and 'role="tab"' in page       # 页签组与页签（方向键移动在 20-shell.js）
    assert 'role="tabpanel"' in page and "aria-labelledby" in page
    assert 'id="detail" hidden role="dialog"' in page              # 抽屉是对话框：焦点进出在 60-events.js
    assert ".sr-only" in page and ":focus-visible" in page         # 读屏专用文字与键盘焦点可见


def test_the_cite_panel_hook_is_declared_and_taken() -> None:
    """第 7 节 WP48 的共享文件请求：renderCite 末尾有可替换挂点，40-evidence 加载时接住它。"""
    assert "let citePanelExtra = null;" in ASSET                   # 挂点声明在 00-core（最先加载）
    assert "if (citePanelExtra) citePanelExtra(model, box);" in ASSET  # renderCite 两个出口都调用
    assert "citePanelExtra = evCiteExtra;" in ASSET                # 40-evidence 直连（观察器绕法已删）
    assert "MutationObserver" not in ASSET                         # 绕法不再出现


# ---------- WP53：OpenDesign kraken 主题（只动 CSS；对比度按对核算，双方案 ≥ 4.5:1） ----------

_TOKENS = (ASSETS / "css" / "00-tokens.css").read_text(encoding="utf-8")


def _token_block() -> tuple[dict[str, str], dict[str, str]]:
    """拆出两套方案的令牌：:root 是默认深色，@media(prefers-color-scheme: light) 里是浅色。"""
    dark, _, rest = _TOKENS.partition("@media (prefers-color-scheme: light)")
    light = rest
    def parse(block: str) -> dict[str, str]:
        found = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", block))
        return found
    return parse(dark), parse(light)


def _luminance(hexcolor: str) -> float:
    rgb = [int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fore: str, back: str) -> float:
    l1, l2 = sorted((_luminance(fore), _luminance(back)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_the_kraken_tokens_are_declared_for_both_schemes() -> None:
    """WP53：令牌换到主人的 OpenDesign kraken 主题（navy 底、紫色 accent），变量名一个不改。"""
    dark, light = _token_block()
    assert dark["--brand"] == "#7132f5" and dark["--bg"] == "#0b1020"      # kraken 紫 + navy 底
    assert dark["--accent"] == "#b7a6ff"                                   # 可读紫（文字用）
    assert light["--brand"] == "#6d28d9"                                   # 浅色方案压深同一色相
    for name in ("--ink", "--soft", "--muted", "--line", "--card", "--lane", "--bg",
                 "--accent", "--accent-bg", "--accent-line", "--good", "--good-bg",
                 "--bad", "--bad-bg", "--warn", "--warn-bg", "--code", "--code-bg",
                 "--brand", "--brand-ink", "--focus"):
        assert dark.get(name), name                                        # 两套方案名字齐全
        assert light.get(name), name
    assert "--grad-brand" in _TOKENS and "--grad-surface" in _TOKENS       # 渐变令牌（无光标的方案里不核算）
    assert "I/C" in (ASSETS / "css" / "10-base.css").read_text(encoding="utf-8")  # 品牌方章


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_body_text_keeps_wcag_aa_in_both_schemes(scheme: str) -> None:
    """WP53：正文与语义文字对各自底色 ≥ 4.5:1（沿用 WP51 的核算口径，套到 kraken 令牌上）。"""
    dark, light = _token_block()
    tokens = dark if scheme == "dark" else light
    pairs = [
        ("--ink", "--bg"), ("--ink", "--card"), ("--ink", "--lane"),
        ("--soft", "--card"), ("--soft", "--lane"),
        ("--muted", "--card"), ("--muted", "--lane"),
        ("--accent", "--card"), ("--accent", "--accent-bg"),
        ("--good", "--good-bg"), ("--bad", "--bad-bg"),
        ("--warn", "--warn-bg"), ("--code", "--code-bg"),
        ("--brand-ink", "--brand"),
    ]
    for fore, back in pairs:
        ratio = _contrast(tokens[fore], tokens[back])
        assert ratio >= 4.5, f"{scheme}: {fore} on {back} is {ratio:.2f}:1"
