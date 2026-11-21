/* 26-screens.js 的纯函数：回放事件标记（WP54）。关键事件是会改变方案或结束运行的节点：
   开始、每次打回、审计发现问题、预算超限、模型失败、收尾；普通产物与模型作答不出现在标记里。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

function loadScreens() {
  const files = ["00-core.js", "05-labels.js", "26-screens.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { marksFor, SCREENS };\n})()`;
  return vm.runInNewContext(wrapper, {});
}

/** 手造一条事件，形状与 journal.jsonl 里的一致 */
function ev(seq, type, actor, data) {
  return { seq, ts: "2026-11-21T00:00:00Z", type, actor, step: "", data, caused_by: [] };
}

test("marksFor：只标关键事件，标签带去向与状态", () => {
  const { marksFor } = loadScreens();
  const marks = marksFor([
    ev(1, "run.started", "app", { mode: "scripted" }),
    ev(2, "model.called", "technical", { output: {} }),
    ev(3, "artifact.written", "technical", { key: "technical/analysis", version: 1 }),
    ev(4, "gate.checked", "audit", { gate: "audit", findings: 2 }),
    ev(5, "route", "flow", { to: "trader", why: "审计发现 2 个问题" }),
    ev(6, "model.failed", "trader", { model: "quick" }),
    ev(7, "gate.checked", "audit", { gate: "audit", findings: 0 }),
    ev(8, "budget.exceeded", "flow", { scope: "run" }),
    ev(9, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ]);
  assert.equal(JSON.stringify(marks), JSON.stringify([
    { seq: 1, label: "开始" },
    { seq: 4, label: "审计发现 2" },
    { seq: 5, label: "打回→交易员" },
    { seq: 6, label: "调用失败" },
    { seq: 8, label: "预算超限" },
    { seq: 9, label: "交付" },
  ]));
});

test("marksFor：通过的闸门不标；REVIEW 的收尾写 REVIEW；空事件给空表", () => {
  const { marksFor } = loadScreens();
  assert.equal(JSON.stringify(marksFor([])), "[]");
  const marks = marksFor([
    ev(1, "run.started", "app", {}),
    ev(2, "gate.checked", "audit", { gate: "audit", findings: 0 }),
    ev(3, "run.finished", "flow", { status: "review", reason: "contested" }),
  ]);
  assert.equal(JSON.stringify(marks),
               JSON.stringify([{ seq: 1, label: "开始" }, { seq: 3, label: "REVIEW" }]));
});

test("SCREENS：三屏的名字固定（empty / setup / work）", () => {
  const { SCREENS } = loadScreens();
  assert.equal(JSON.stringify(SCREENS), '["empty","setup","work"]');
});
