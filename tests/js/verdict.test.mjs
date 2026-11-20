/* 结论视图（WP46，BLUEPRINT 20.10）的纯推导：四态判定、进度行、REVIEW 建议、交付字段。
   30-verdict.js 的下半才碰 DOM，这里只装它够用的前半（00-core / 05-labels / 10-model + 本视图）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import { ev } from "./helpers.mjs";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

function loadVerdict() {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "30-verdict.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { fold, verdictStatusOf, verdictProgress, verdictAdvice, verdictData, verdictStageOf, waitingApprovalOf, approvalReasonText };\n})()`;
  return vm.runInNewContext(wrapper, {});
}

const START = [ev(1, "run.started", "app", { mode: "scripted", request: { ticker: "ACME", as_of: "2026-09-30" } })];

function deliveredRun() {
  /* 一次最小交付运行：账本工具 → 主管论点（摘要、论点与引用、失效条件）→ 方案 → 结果。 */
  return [...START,
    ev(2, "artifact.written", "ledger", { key: "ledger/instrument", version: 1,
      value: { ticker: "ACME", currency: "USD" } }),
    ev(3, "artifact.written", "ledger", { key: "ledger/facts", version: 1,
      value: [{ id: "sma_20", label: "20 日均线", value: 99.25, unit: "USD", as_of: "2026-09-30" }] }),
    ev(4, "artifact.written", "manager", { key: "manager/thesis", version: 1,
      value: { rating: "Overweight", horizon_days: 10, summary: "均线向上，基本面稳。",
               points: [{ text: "均线向上", cites: ["sma_20"] }],
               invalidation: ["跌破 20 日均线"] } }, "manager#1"),
    ev(5, "artifact.written", "trader", { key: "trader/proposal", version: 1,
      value: { action: "Buy", entry: 100, stop: 95, target: 110, size_pct: 8, horizon_days: 10,
               cites: ["sma_20"], rationale: "顺势建仓" } }, "trader#1"),
    ev(6, "artifact.written", "flow", { key: "run/outcome", version: 1,
      value: { status: "delivered", reason: "ok", facts_and_rules: "verified", judgment: "accepted 2/3" } }),
    ev(7, "run.finished", "flow", { status: "delivered", reason: "ok" })];
}

test("verdictStatusOf：四态——直播运行、交付、REVIEW、中断（含直播流已收尾）", () => {
  const { fold, verdictStatusOf } = loadVerdict();
  const running = fold(START, null, true);
  assert.equal(verdictStatusOf(running, true, false), "running");
  const done = fold(deliveredRun(), null, false);
  assert.equal(verdictStatusOf(done, false, true), "delivered");
  const review = fold([...START, ev(9, "run.finished", "flow", { status: "review", reason: "contested" })], null, false);
  assert.equal(verdictStatusOf(review, true, true), "review");
  const torn = fold([...START, ev(9, "model.called", "bull", {}, "debate#1")], null, true);
  assert.equal(verdictStatusOf(torn, true, true), "interrupted");   // end 已到而没有 run.finished：被取消或崩溃
  assert.equal(verdictStatusOf(torn, false, true), "interrupted");  // 静态页缺 run.finished 同样是中断
  assert.equal(verdictStatusOf(torn, true, false), "running");      // 流还开着：仍在跑
});

test("verdictProgress：已进行到哪一段，进度行带上调用与事件计数", () => {
  const { verdictProgress } = loadVerdict();
  const mid = [...START,
    ev(2, "model.called", "technical", { output: {} }, "technical#1"),
    ev(3, "artifact.written", "technical", { key: "technical/analysis", version: 1, value: {} }),
    ev(4, "model.called", "bull", { output: {} }, "debate#1")];
  const at = verdictProgress(mid);
  assert.equal(at.stage, "debate");
  assert.equal(at.calls, 2);
  assert.match(at.line, /「多空辩论」段；模型调用 2 次，事件 4 条/);
  assert.equal(verdictProgress([]).line, "正在等待第一条事件。");
  const intakeOnly = verdictProgress([...START, ev(2, "gate.checked", "audit", { gate: "intake", findings: 0, detail: [] })]);
  assert.equal(intakeOnly.stage, "intake");
});

test("verdictAdvice：REVIEW 的下一步——只 20.10 点名的三种，其余没有建议", () => {
  const { verdictAdvice } = loadVerdict();
  assert.equal(verdictAdvice("budget_exhausted").tab, null);
  assert.match(verdictAdvice("budget_exhausted").text, /提高预算上限/);
  assert.equal(verdictAdvice("contested").tab, "process");
  assert.match(verdictAdvice("contested").text, /反对意见/);
  assert.match(verdictAdvice("model_unavailable").text, /续跑/);
  assert.equal(verdictAdvice("audit_failed"), null);
  assert.equal(verdictAdvice("ok"), null);
});

test("verdictData：交付结论的全部字段（评级、价位、货币、摘要、论点引用、失效条件）", () => {
  const { fold, verdictData } = loadVerdict();
  const data = verdictData(fold(deliveredRun(), null, false));
  assert.equal(data.outcome.status, "delivered");
  assert.equal(data.currency, "USD");
  assert.equal(data.thesis.rating, "Overweight");
  assert.equal(data.thesis.summary, "均线向上，基本面稳。");
  assert.equal(data.thesis.points.length, 1);
  assert.equal(data.thesis.points[0].cites[0], "sma_20");
  assert.equal(data.thesis.invalidation[0], "跌破 20 日均线");
  assert.equal(data.proposal.action, "Buy");
  assert.equal(data.proposal.entry, 100);
  assert.equal(data.proposal.size_pct, 8);
  assert.equal(data.proposal.horizon_days, data.thesis.horizon_days);
  const empty = verdictData(fold(START, null, false));   // 什么都没写：一律空而不抛
  assert.equal(empty.outcome, null);
  assert.equal(empty.thesis, null);
  assert.equal(empty.proposal, null);
  assert.equal(empty.currency, "");
});

test("verdictStageOf：产物按键前缀与首段归段，评审按执行者归段", () => {
  const { verdictStageOf } = loadVerdict();
  assert.equal(verdictStageOf(ev(2, "artifact.written", "judge.evidence",
    { key: "judge.evidence/verdict", version: 1, value: {} })), "judges");
  assert.equal(verdictStageOf(ev(2, "artifact.written", "news",
    { key: "macro/facts", version: 1, value: [] })), "news");
  assert.equal(verdictStageOf(ev(2, "artifact.written", "manager",
    { key: "manager/thesis", version: 1, value: {} })), "manager");
  assert.equal(verdictStageOf(ev(2, "artifact.written", "portfolio",
    { key: "portfolio/state", version: 1, value: {} })), null);   // 不属于任何流水线段
  assert.equal(verdictStageOf(ev(2, "model.failed", "risk.aggressive", {}, "risk#1")), "risk");
});

test("waitingApprovalOf：审批门暂停的判定（WP52，20.11）——到了门口才算等待", () => {
  const { fold, waitingApprovalOf } = loadVerdict();
  const start = (flow) => [ev(1, "run.started", "app",
    { mode: "scripted", request: { ticker: "ACME", as_of: "2026-09-30" }, flow: flow })];
  const gate = (flow) => [...start(flow),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: {} }, "trader#1"),
    ev(3, "artifact.written", "auditor", { key: "audit/status", version: 1,
      value: { verdict: "pass", proposal_seq: 2, thesis_seq: 1, analysis_seq: 0 } }),
    ev(4, "artifact.written", "flow", { key: "judges/status", version: 1, value: "accepted 2/3" })];
  /* 到了门口、没人给过决定：等待（直播流已收尾的暂停，与静态页上的暂停同样判等待） */
  assert.equal(waitingApprovalOf(fold(gate({ approve: true }), null, false), true, true), true);
  assert.equal(waitingApprovalOf(fold(gate({ approve: true }), null, false), false, true), true);
  /* 门没开（默认）、决定已在板上、approve 开着但还没到门口（评审没给结论）：都不是等待 */
  assert.equal(waitingApprovalOf(fold(gate({}), null, false), true, true), false);
  assert.equal(waitingApprovalOf(fold([...gate({ approve: true }),
    ev(5, "artifact.written", "user", { key: "approval/decision", version: 1,
      value: { decision: "approved", note: "" } })], null, false), true, true), false);
  assert.equal(waitingApprovalOf(fold([...start({ approve: true }),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: {} })],
    null, false), true, true), false);
  /* 评审关闭时以审计通过为准：门口成立即等待 */
  assert.equal(waitingApprovalOf(fold(gate({ approve: true, judges: false }), null, false), true, true), true);
  /* 没到门口的普通中断（比如辩论中取消）不因 approve 开着就算等待 */
  assert.equal(waitingApprovalOf(fold([...start({ approve: true }),
    ev(2, "model.called", "bull", { output: {} }, "debate#1")], null, false), true, true), false);
});

test("approvalReasonText：rejected_by_user 用本视图的私有标签，其余照旧落生键名", () => {
  const { approvalReasonText } = loadVerdict();
  assert.equal(approvalReasonText("rejected_by_user"), "你否决了这次方案");
  assert.equal(approvalReasonText("contested"), "评审多数反对，未达成共识");
});
