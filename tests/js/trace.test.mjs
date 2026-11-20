/* 轨迹到跨度（BLUEPRINT 20.12 点名的第五项）：配对、没收尾的跨度、耗时进用量。 */
import test from "node:test";
import assert from "node:assert/strict";
import { loadBelow20, ev } from "./helpers.mjs";

test("spans：role.started/finished 配对，没收尾的跨度 end 为 null", () => {
  const { fold } = loadBelow20();
  const events = [
    ev(1, "run.started", "app", {}),
    ev(2, "model.called", "technical", { model: "m", prompt: "", messages: [], output: { claims: [] },
      usage: { requests: 1, input_tokens: 10, output_tokens: 5 }, cost: null }, "technical#1"),
  ];
  const trace = [
    { ts: "2026-11-20T00:00:01Z", kind: "role.started", step: "technical#1", role: "technical", tier: "quick", model: "m" },
    { ts: "2026-11-20T00:00:01Z", kind: "role.started", step: "bull#1", role: "bull", tier: "quick", model: "m" },
    { ts: "2026-11-20T00:00:03Z", kind: "role.finished", step: "technical#1", role: "technical", ms: 2000, ok: true,
      requests: 1, input_tokens: 10, output_tokens: 5, tool_calls: 0 },
  ];
  const model = fold(events, trace, false);
  assert.equal(model.spans.length, 2);
  const done = model.spans.find((s) => s.role === "technical");
  assert.equal(done.step, "technical#1");
  assert.equal(done.model, "m");
  assert.equal(done.start, "2026-11-20T00:00:01Z");
  assert.equal(done.end, "2026-11-20T00:00:03Z");
  assert.equal(done.ms, 2000);
  assert.equal(done.ok, true);
  const open = model.spans.find((s) => s.role === "bull");
  assert.equal(open.end, null);                 // 只有开始没收尾：飞行中，或被打断
  // 调用按 step|role 配到自己的跨度：耗时上调用，也进该角色与合计的用量
  assert.equal(model.calls[0].ms, 2000);
  assert.equal(model.usage.roles.technical.ms, 2000);
  assert.equal(model.usage.total.ms, 2000);
  assert.equal(model.usage.total.tokens, 15);
});

test("spans：role.failed 收尾的跨度 ok 为 false", () => {
  const { fold } = loadBelow20();
  const trace = [
    { ts: "2026-11-20T00:00:01Z", kind: "role.started", step: "risk.aggressive#1", role: "risk.aggressive", model: "m" },
    { ts: "2026-11-20T00:00:02Z", kind: "role.failed", step: "risk.aggressive#1", role: "risk.aggressive", ms: 50 },
  ];
  const spans = fold([ev(1, "run.started", "app", {})], trace, false).spans;
  assert.equal(spans.length, 1);
  assert.equal(spans[0].ok, false);
  assert.notEqual(spans[0].end, null);
  assert.equal(spans[0].ms, 50);
});

test("fold：没有轨迹时调用与跨度的耗时都是空，用量照算", () => {
  const { fold } = loadBelow20();
  const model = fold([
    ev(1, "run.started", "app", {}),
    ev(2, "model.called", "trader", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 2, input_tokens: 10, output_tokens: 5 }, cost: 0.01, tool_calls: 3 }, "trader#1"),
  ], null, false);
  assert.equal(model.calls.length, 1);
  assert.equal(model.calls[0].ms, null);
  assert.equal(model.calls[0].retried, true);   // usage.requests > 1 记一次纠正重试
  assert.equal(model.calls[0].tool_calls, 3);
  assert.equal(model.spans.length, 0);
  assert.equal(model.usage.roles.trader.calls, 1);
  assert.equal(model.usage.roles.trader.cost, 0.01);
});
