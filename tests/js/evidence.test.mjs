/* 证据页签（WP48，BLUEPRINT 20.10「Evidence」）的纯推导：统一事实表的行与搜索；
   以及引用抽屉挂点（citePanelExtra，第 7 节共享文件请求由 WP51 落实）的直连。
   装载器仿 helpers.mjs，把 40-evidence.js 一并载入——它加载时不碰 document/window（给挂点赋值不算）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

/* 假 DOM：只实现 el 用到的那几个方法（与 usage.test.mjs 同款的最小集） */
class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.style = {};
                    this.className = ""; this.listeners = {}; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] || (this.listeners[name] = [])).push(fn); }
}
const textOf = (node) => node.children.map((kid) => kid instanceof FakeNode ? textOf(kid) : String(kid)).join("");

function loadEvidence(stateSnapshot) {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "40-evidence.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const state = JSON.stringify({ cited: "", ...(stateSnapshot || {}) });
  const wrapper = `(function () {\n"use strict";\n${source}\nconst state = ${state};\n`
    + "return { fold, evFactRows, evFactMatches, evActorName, evReadings, evCiteExtra, citePanelExtra, FACT_KIND_TEXT };\n})()";
  return vm.runInNewContext(wrapper, { document: { createElement: (t) => new FakeNode(t) }, Node: FakeNode });
}

/** 手造一次带四类事实与多条分析的运行（形状与 journal.jsonl 一致） */
function richModel(fold) {
  return fold([
    { seq: 1, ts: "2026-11-20T00:00:00Z", type: "run.started", actor: "app", step: "", data: {}, caused_by: [] },
    { seq: 2, ts: "", type: "artifact.written", actor: "ledger", step: "", caused_by: [], data: {
      key: "ledger/facts", version: 1, value: [
        { id: "sma_20", label: "20 日均线", value: 222.257, unit: "USD", as_of: "2026-09-30", source: "indicators.sma", bars_used: 20 },
        { id: "close", label: "收盘价", value: 230.85, unit: "USD", as_of: "2026-09-30", source: "market.close", bars_used: 1 }] } },
    { seq: 3, ts: "", type: "artifact.written", actor: "evidence", step: "", caused_by: [], data: {
      key: "fundamentals/facts", version: 1, value: [
        { id: "revenue_fy", label: "营业收入（最近财年）", value: 1081.0, unit: "USD million", as_of: "2026-08-14", source: "fundamentals.latest", bars_used: 0 }] } },
    { seq: 4, ts: "", type: "artifact.written", actor: "technical", step: "technical#1", caused_by: [], data: {
      key: "tools/facts", version: 1, value: [{ id: "sma_30", value: 99.25, unit: "USD", as_of: "2026-09-30" }] } },
    { seq: 5, ts: "", type: "artifact.written", actor: "news", step: "", caused_by: [], data: {
      key: "macro/facts", version: 1, value: [
        { id: "cpi_yoy", label: "CPI 同比", value: 3.1, unit: "%", as_of: "2026-09-30", source: "macro.cpi", bars_used: 0 }] } },
    { seq: 6, ts: "", type: "artifact.written", actor: "technical", step: "technical#1", caused_by: [], data: {
      key: "technical/analysis", version: 1, value: { narrative: "趋势向上",
        claims: [{ fact: "sma_20", value: 222.257, reading: "均线多头排列" }] } } },
    { seq: 7, ts: "", type: "artifact.written", actor: "fundamentals", step: "fundamentals#1", caused_by: [], data: {
      key: "fundamentals/analysis", version: 1, value: { narrative: "收入增长",
        claims: [{ fact: "revenue_fy", value: 1081.0, reading: "收入创新高" }] } } },
    { seq: 8, ts: "", type: "artifact.written", actor: "news", step: "news#1", caused_by: [], data: {
      key: "news/analysis", version: 1, value: { narrative: "宏观平稳",
        claims: [{ fact: "cpi_yoy", value: 3.1, reading: "通胀温和" }] } } },
    { seq: 9, ts: "", type: "artifact.written", actor: "manager", step: "manager#1", caused_by: [], data: {
      key: "manager/thesis", version: 1, value: { rating: "Overweight", horizon_days: 10,
        points: [{ text: "趋势与基本面共振", cites: ["sma_20", "revenue_fy"] }] } } },
    { seq: 10, ts: "", type: "artifact.written", actor: "trader", step: "trader#1", caused_by: [], data: {
      key: "trader/proposal", version: 1, value: { action: "Buy", entry: 231, stop: 220, target: 250,
        size_pct: 5, horizon_days: 10, cites: ["close"] } } },
    { seq: 11, ts: "", type: "run.finished", actor: "flow", step: "", caused_by: [],
      data: { status: "delivered", reason: "ok" } },
  ], null, false);
}

test("evFactRows：四类事实合成一张表，带种类徽章文案、解读与引用它的角色", () => {
  const { fold, evFactRows, FACT_KIND_TEXT } = loadEvidence();
  const rows = evFactRows(richModel(fold));
  assert.equal(JSON.stringify(rows.map((r) => r.id)),
    JSON.stringify(["sma_20", "close", "revenue_fy", "sma_30", "cpi_yoy"]));
  assert.equal(JSON.stringify(rows.map((r) => r.kind)),
    JSON.stringify(["ledger", "ledger", "fundamentals", "tool", "macro"]));
  assert.equal(JSON.stringify(rows.map((r) => r.kindText)),
    JSON.stringify([FACT_KIND_TEXT.ledger, FACT_KIND_TEXT.ledger,
                    FACT_KIND_TEXT.fundamentals, FACT_KIND_TEXT.tool, FACT_KIND_TEXT.macro]));
  assert.equal(JSON.stringify(FACT_KIND_TEXT), JSON.stringify(
    { ledger: "账本", fundamentals: "基本面", tool: "工具", macro: "宏观" }));
  const by = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.equal(by.sma_20.readings.join(), "均线多头排列");          // 技术面 claim 的解读进了表
  assert.equal(by.cpi_yoy.readings.join(), "通胀温和");             // 新闻（宏观）claim 同样进表
  assert.equal(by.sma_30.readings.length, 0);                      // 工具事实没人解读过
  assert.equal(by.sma_30.label, "（工具按需计算）");                // 工具事实没有含义：占位而非空白
  assert.equal(by.close.citers.join("、"), "交易员");
  assert.equal(by.sma_20.citers.join("、"), "技术分析师、研究主管");  // 去重、按首次出现序
  assert.equal(by.revenue_fy.citers.join("、"), "基本面分析师、研究主管");
});

test("evFactMatches：搜索覆盖编号、含义、数值、单位、数据日、种类、解读与引用者", () => {
  const { fold, evFactRows, evFactMatches } = loadEvidence();
  const rows = evFactRows(richModel(fold));
  const ids = (q) => rows.filter((row) => evFactMatches(row, q)).map((row) => row.id);
  assert.equal(JSON.stringify(ids("")), JSON.stringify(["sma_20", "close", "revenue_fy", "sma_30", "cpi_yoy"]));
  assert.equal(JSON.stringify(ids("均线")), JSON.stringify(["sma_20"]));       // 含义
  assert.equal(JSON.stringify(ids("usd")), JSON.stringify(["sma_20", "close", "revenue_fy", "sma_30"]));  // 单位（含 "USD million"），不区分大小写
  assert.equal(JSON.stringify(ids("99")), JSON.stringify(["sma_30"]));        // 数值
  assert.equal(JSON.stringify(ids("2026-08-14")), JSON.stringify(["revenue_fy"]));           // 数据日
  assert.equal(JSON.stringify(ids("工具")), JSON.stringify(["sma_30"]));      // 种类徽章
  assert.equal(JSON.stringify(ids("通胀")), JSON.stringify(["cpi_yoy"]));     // 解读
  assert.equal(JSON.stringify(ids("研究主管")), JSON.stringify(["sma_20", "revenue_fy"]));    // 引用者
  assert.equal(ids("不存在的词").length, 0);
});

test("evActorName：泳道、组名与原文逐级回落，绝不空白", () => {
  const { evActorName } = loadEvidence();
  assert.equal(evActorName("technical"), "技术分析师");
  assert.equal(evActorName("risk.aggressive"), "激进风控");
  assert.equal(evActorName("judge.evidence"), "证据评审");
  assert.equal(evActorName("evidence"), "证据");                       // 组名之外：LANES 兜住
  assert.equal(evActorName("someone-else"), "someone-else");           // 未知：回落原文
});

test("citePanelExtra：renderCite 的挂点在加载时接住——已知编号追加「在证据中查看」，未知引用不加", () => {
  const known = loadEvidence({ cited: "sma_20" });
  assert.equal(typeof known.citePanelExtra, "function");              // 40-evidence 加载时已把挂点设上
  const model = richModel(known.fold);
  const box = new FakeNode("aside");
  known.citePanelExtra(model, box);
  assert.equal(box.children.length, 1);                               // 只追加一个按钮容器
  assert.ok(textOf(box).includes("在证据中查看"));

  const unknown = loadEvidence({ cited: "nope" });
  const box2 = new FakeNode("aside");
  unknown.citePanelExtra(model, box2);
  assert.equal(box2.children.length, 0);                              // 未知引用：挂点自己判空，什么都不加
});
