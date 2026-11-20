/* fold 的核心行为：黑板与版本史、审计轮次、引用索引、运行状态（BLUEPRINT 20.12 点名的前四项）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { loadBelow20, ev } from "./helpers.mjs";

test("fold：黑板取每键最新值，版本史标记被取代的版本", () => {
  const { fold } = loadBelow20();
  const model = fold([
    ev(1, "run.started", "app", { mode: "scripted" }),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: { entry: 100 } }, "trader#1"),
    ev(3, "artifact.written", "trader", { key: "trader/proposal", version: 2, value: { entry: 101 } }, "trader#2"),
    ev(4, "artifact.written", "flow", { key: "run/outcome", version: 1, value: { status: "delivered" } }),
  ], null, false);
  assert.equal(model.board["trader/proposal"].entry, 101);
  const versions = model.versions["trader/proposal"];
  assert.equal(versions.length, 2);
  // fold 在 vm 作用域里求值，对象跨 realm：结构一律用 JSON 比较
  assert.equal(JSON.stringify(versions.map((v) => [v.version, v.superseded])),
               JSON.stringify([[1, true], [2, false]]));
  assert.equal(versions[0].by, "trader");
  assert.equal(versions[0].seq, 2);
  assert.equal(versions[1].step, "trader#2");
});

test("fold：审计轮次——方案的每一版配上检查它的闸门与被打回的去向", () => {
  const { fold } = loadBelow20();
  const model = fold([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: { entry: 100 } }, "trader#1"),
    ev(3, "gate.checked", "audit", { gate: "audit", findings: 1,
      detail: [{ check: "stop_required", subject: "proposal", message: "买入必须给出止损价" }] }),
    ev(4, "route", "flow", { to: "trader", why: "审计发现 1 个问题" }),
    ev(5, "artifact.written", "trader", { key: "trader/proposal", version: 2, value: { entry: 100, stop: 95 } }, "trader#2"),
    ev(6, "gate.checked", "audit", { gate: "audit", findings: 0, detail: [] }),
    ev(7, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ], null, false);
  assert.equal(model.rounds.length, 2);
  const [first, second] = model.rounds;
  assert.equal(first.version, 1);
  assert.equal(first.gate.seq, 3);
  assert.equal(first.findings.length, 1);
  assert.equal(first.findings[0].check, "stop_required");
  assert.equal(first.route.data.to, "trader");
  assert.equal(second.version, 2);
  assert.equal(second.gate.seq, 6);
  assert.equal(second.findings.length, 0);
  assert.equal(second.route, null);
  // 闸门前于方案的那一版（被打回后还没重写）没有对应的轮次可配：gate 为 null
  const stalled = fold([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: { entry: 100 } }, "trader#1"),
    ev(3, "run.finished", "flow", { status: "review", reason: "audit_failed" }),
  ], null, false);
  assert.equal(stalled.rounds.length, 1);
  assert.equal(stalled.rounds[0].gate, null);
});

test("fold：cited_by 收集 fact/item/post 键与 cites 数组里的编号", () => {
  const { fold } = loadBelow20();
  const model = fold([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "ledger", { key: "ledger/facts", version: 1,
      value: [{ id: "sma_20", label: "20 日均线", value: 9.5, unit: "USD", as_of: "2026-09-30" }] }),
    ev(3, "artifact.written", "technical", { key: "technical/analysis", version: 1,
      value: { narrative: "趋势向上", claims: [{ fact: "sma_20", value: 9.5, reading: "均线向上" }] } }, "technical#1"),
    ev(4, "artifact.written", "manager", { key: "manager/thesis", version: 1,
      value: { rating: "Overweight", horizon_days: 10, points: [{ text: "均线向上", cites: ["sma_20"] }] } }, "manager#1"),
    ev(5, "artifact.written", "disclosures", { key: "disclosures/analysis", version: 1,
      value: { stance: "positive", confidence: 3, signals: [{ item: "D1", direction: "positive", reading: "回购" }] } }),
    ev(6, "artifact.written", "sentiment", { key: "sentiment/analysis", version: 1,
      value: { band: "mildly_bullish", score: 6.5, confidence: 3, signals: [{ post: "S1", direction: "positive", reading: "看多" }] } }),
  ], null, false);
  assert.equal(JSON.stringify(model.cited_by["sma_20"]), JSON.stringify([
    { actor: "technical", key: "technical/analysis", seq: 3 },
    { actor: "manager", key: "manager/thesis", seq: 4 }]));
  assert.equal(JSON.stringify(model.cited_by["D1"]),
               JSON.stringify([{ actor: "disclosures", key: "disclosures/analysis", seq: 5 }]));
  assert.equal(JSON.stringify(model.cited_by["S1"]),
               JSON.stringify([{ actor: "sentiment", key: "sentiment/analysis", seq: 6 }]));
  assert.equal(model.cited_by["ghost"], undefined);
  // 事实与条目各带种类；账本事实是 ledger，工具与宏观另有来源
  assert.equal(model.facts["sma_20"].kind, "ledger");
  assert.equal(model.facts["sma_20"].label, "20 日均线");
});

test("fold：四种状态——运行中、交付、REVIEW、已中断", () => {
  const { fold } = loadBelow20();
  const base = [ev(1, "run.started", "app", {})];
  const finished = (status) => ev(9, "run.finished", "flow", { status, reason: "ok" });
  assert.equal(fold(base, null, true).status, "running");
  assert.equal(fold([...base, finished("delivered")], null, false).status, "delivered");
  assert.equal(fold([...base, finished("review")], null, true).status, "review");
  assert.equal(fold(base, null, false).status, "interrupted");
});
