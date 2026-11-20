/* 25-activity.js 的核心行为：流水线每段的状态（等待/进行中/完成/被打回/跳过）、
   总用时与在飞角色的计时（BLUEPRINT 20.10「实时活动」）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

/* 25/55 号文件在加载时可能碰 DOM：给一个查什么都没有的 document，加载分支就整体跳过，
   纯函数照常可用（页面里它们与 90 号文件共用一个函数作用域，BLUEPRINT 20.9）。 */
function loadViews() {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "25-activity.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { pipeline, stageOf, stageState, activityRoles, elapsedText, runElapsed, tsMs };\n})()`;
  return vm.runInNewContext(wrapper, { document: { getElementById: () => undefined, querySelector: () => null } });
}

/** 手造一条事件，形状与 journal.jsonl 里的一致；ts 可给 */
function ev(seq, type, actor, data, step = "", ts = "2026-11-20T00:00:00Z") {
  return { seq, ts, type, actor, step, data, caused_by: [] };
}

const at = (offsetSec) => new Date(Date.parse("2026-11-20T00:00:00Z") + offsetSec * 1000).toISOString();

/** 一段没有返工的运行的前半截：准入、账本、技术分析、主管、交易员、审计通过 */
function baseRun() {
  return [
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "intake", { key: "request/scope", version: 1, value: {} }),
    ev(3, "artifact.written", "ledger", { key: "ledger/facts", version: 1, value: [] }),
    ev(4, "model.called", "technical", { output: { narrative: "x" }, usage: { requests: 1 } }, "technical#1"),
    ev(5, "model.called", "manager", { output: {}, usage: { requests: 1 } }, "manager#1"),
    ev(6, "model.called", "trader", { output: {}, usage: { requests: 1 } }, "trader#1"),
    ev(7, "gate.checked", "audit", { gate: "audit", findings: 0, detail: [] }),
  ];
}

test("pipeline：完成、等待与进行中；闸门通过后轮到下一段", () => {
  const { pipeline } = loadViews();
  const mid = pipeline(baseRun());
  assert.equal(mid.status.intake, "done");
  assert.equal(mid.status.ledger, "done");
  assert.equal(mid.status.technical, "done");
  assert.equal(mid.status.trader, "done");
  assert.equal(mid.status.audit, "done");
  assert.equal(mid.status.judges, "running");            // 审计通过 ⇒ 接下来是评审
  assert.equal(mid.status.debate, undefined);            // 还没轮到：等待
  const done = pipeline([...baseRun(), ev(8, "run.finished", "flow", { status: "delivered", reason: "ok" })]);
  assert.equal(done.status.judges, undefined);           // 评审关闭时它不再标进行中
  assert.equal(done.status.report, "done");
});

test("pipeline：已中断的运行，停下的那一段画「未完成」而不是「进行中」", () => {
  const { pipeline } = loadViews();
  const stopped = pipeline(baseRun(), true);             // 回放/已中断：没有 run.finished 也没有活的流
  assert.equal(stopped.status.judges, "halted");
  assert.equal(pipeline(baseRun(), false).status.judges, "running");
});

test("pipeline：被打回——目标段记一次返工、重跑后完成，展示算「被打回 ↺n」", () => {
  const { pipeline, stageState } = loadViews();
  const sent = pipeline([...baseRun().slice(0, 6),
    ev(7, "gate.checked", "audit", { gate: "audit", findings: 1, detail: [{ check: "stop_required" }] }),
    ev(8, "route", "flow", { to: "trader", why: "审计发现 1 个问题" })]);
  assert.equal(sent.redo.trader, 1);
  assert.equal(sent.status.trader, "running");           // 打回后从交易员重跑
  const fixed = pipeline([...baseRun().slice(0, 6),
    ev(7, "gate.checked", "audit", { gate: "audit", findings: 1, detail: [{ check: "stop_required" }] }),
    ev(8, "route", "flow", { to: "trader", why: "审计发现 1 个问题" }),
    ev(9, "model.called", "trader", { output: {}, usage: { requests: 1 } }, "trader#2"),
    ev(10, "gate.checked", "audit", { gate: "audit", findings: 0, detail: [] }),
    ev(11, "run.finished", "flow", { status: "delivered", reason: "ok" })]);
  assert.equal(fixed.status.trader, "done");
  assert.equal(fixed.redo.trader, 1);
  assert.equal(stageState(fixed, "trader"), "sentback"); // 完成但被打回过：展示层算「被打回」
  assert.equal(stageState(fixed, "manager"), "done");
  assert.equal(stageState(fixed, "debate"), "waiting");
});

test("pipeline：通道不可用 ⇒ 那一段跳过（状态键由证据阶段代写，不归 ledger 段）", () => {
  const { pipeline } = loadViews();
  const info = pipeline([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "evidence", { key: "fundamentals/status", version: 1,
      value: { channel: "fundamentals", state: "unavailable", reason: "尚未接入数据源" } }),
    ev(3, "model.called", "technical", { output: {}, usage: { requests: 1 } }, "technical#1"),
    ev(4, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ]);
  assert.equal(info.channels.fundamentals.state, "unavailable");
  assert.equal(info.channels.fundamentals.reason, "尚未接入数据源");
  assert.equal(info.status.fundamentals, "skipped");
  assert.equal(info.status.ledger, "done");              // 状态键本身算证据（账本段）的工作
  const ok = pipeline([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "evidence", { key: "fundamentals/status", version: 1,
      value: { channel: "fundamentals", state: "available", reason: "" } }),
    ev(3, "model.called", "fundamentals", { output: {}, usage: { requests: 1 } }, "fundamentals#1"),
    ev(4, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ]);
  assert.equal(ok.status.fundamentals, "done");          // 可用的通道照常完成
});

test("elapsedText / runElapsed：总用时，没有结束事件时实时走到现在、回放停在最后一条", () => {
  const { elapsedText, runElapsed } = loadViews();
  assert.equal(elapsedText(0), "0:00");
  assert.equal(elapsedText(65_000), "1:05");
  assert.equal(elapsedText(3_723_000), "1:02:03");
  assert.equal(elapsedText(null), "—");
  const started = ev(1, "run.started", "app", {}, "", at(0));
  const running = [started, ev(2, "model.called", "technical", { output: {}, usage: {} }, "technical#1", at(3))];
  assert.equal(runElapsed([...running, ev(3, "run.finished", "flow", {}, "", at(10))], null), 10_000);
  assert.equal(runElapsed(running, Date.parse(at(7))), 7_000);   // 实时：最后一条之后仍在走
  assert.equal(runElapsed(running, null), 3_000);                // 回放/已中断：停在最后已知时刻
  assert.equal(runElapsed([ev(1, "model.called", "technical", {}, "technical#1", at(1))], null), null);  // 没有 run.started
});

test("activityRoles：轨迹里开着头没收尾的调用各一行，收了尾的不算", () => {
  const { activityRoles } = loadViews();
  const spans = [
    { step: "technical#1", role: "technical", start: at(10), end: null, ms: null },
    { step: "fundamentals#1", role: "fundamentals", start: at(12), end: at(20), ms: 8000 },
    { step: "news#1", role: "news", start: at(11), end: null, ms: null },
  ];
  const roles = activityRoles(spans, Date.parse(at(19)));
  assert.equal(roles.length, 2);
  assert.equal(roles[0].role, "technical");              // 先开始的排前面
  assert.equal(roles[0].elapsedMs, 9_000);
  assert.equal(roles[0].label, "技术分析师");             // 角色名取自泳道表
  assert.equal(roles[1].role, "news");
  assert.equal(roles[1].label, "新闻分析师");
  assert.equal(activityRoles(spans, null).length, 0);    // 没有可依据的「现在」就不画计时器
});
