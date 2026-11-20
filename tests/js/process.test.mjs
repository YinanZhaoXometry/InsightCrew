/* 过程页签（WP47，35-process.js）的纯推导：出场与产物归属、辩论/风险/评审的轮次、
   修订对照（审计打回 / 评审否决 / 随上游重做 / 主动修订不标记）、风险后的修订。
   35-process.js 加载时不碰 document，与编号 20 以下的源文件一起在 vm 里求值。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import { ev } from "./helpers.mjs";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

function loadProcess() {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "35-process.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { fold, processTurns, debateRounds, riskRounds, judgeRounds,
  revisionsOf, riskRevision, fieldText, PROPOSAL_ROWS, THESIS_ROWS };\n})()`;
  return vm.runInNewContext(wrapper, {});
}

const call = (seq, actor, step, output, causedBy = []) =>
  ({ ...ev(seq, "model.called", actor, { model: "m", prompt: "", messages: [], output,
    usage: { requests: 1, input_tokens: 100, output_tokens: 40 }, cost: null }, step), caused_by: causedBy });
const art = (seq, actor, key, version, value, causedBy = []) =>
  ({ ...ev(seq, "artifact.written", actor, { key, version, value }, ""), caused_by: causedBy });
const gate = (seq, findings, detail = []) =>
  ev(seq, "gate.checked", "audit", { gate: "audit", findings, detail });
const route = (seq, to, why) => ev(seq, "route", "flow", { to, why });

test("processTurns：产物按 caused_by 归到生产它的那次出场；旧版本标记 superseded", () => {
  const p = loadProcess();
  const events = [
    ev(1, "run.started", "app", {}),
    call(2, "technical", "technical#1", { claims: [], narrative: "v1" }),
    art(3, "technical", "technical/analysis", 1, { claims: [], narrative: "v1" }, [2]),
    call(4, "technical", "technical#2", { claims: [], narrative: "v2" }),
    art(5, "technical", "technical/analysis", 2, { claims: [], narrative: "v2" }, [4]),
    call(6, "trader", "trader#1", null),                     // 纠正一次后仍不合契约：没有产物
    call(7, "trader", "trader#2", { action: "Buy", size_pct: 5 }),
    art(8, "trader", "trader/proposal", 1, { action: "Buy", size_pct: 5 }, [7]),
  ];
  const model = p.fold(events, null, false);
  const turns = p.processTurns(model);
  assert.equal(turns.length, 4);
  const shape = turns.map((t) => [t.actor, t.key, t.version, t.superseded]);
  assert.equal(JSON.stringify(shape), JSON.stringify([
    ["technical", "technical/analysis", 1, true],
    ["technical", "technical/analysis", 2, false],
    ["trader", "", 0, false],                                // 不合契约的调用：没有产物，不算被取代
    ["trader", "trader/proposal", 1, false],
  ]));
});

test("辩论、风险、评审的轮次：按各角色第 n 次出场配对；评审一轮以 judges/status 收尾", () => {
  const p = loadProcess();
  const events = [
    ev(1, "run.started", "app", {}),
    call(2, "bull", "bull#1", { points: [], rebuttal: "" }),
    art(3, "bull", "bull/argument", 1, { points: [], rebuttal: "" }, [2]),
    call(4, "bear", "bear#1", { points: [], rebuttal: "" }),
    art(5, "bear", "bear/argument", 1, { points: [], rebuttal: "" }, [4]),
    call(6, "bull", "bull#2", { points: [], rebuttal: "回" }),
    art(7, "bull", "bull/argument", 2, { points: [], rebuttal: "回" }, [6]),
    call(8, "bear", "bear#2", { points: [], rebuttal: "回" }),
    art(9, "bear", "bear/argument", 2, { points: [], rebuttal: "回" }, [8]),
    call(10, "manager", "manager#1", { rating: "Buy", horizon_days: 10, points: [], invalidation: [], summary: "" }),
    art(11, "manager", "manager/thesis", 1, { rating: "Buy", horizon_days: 10, summary: "" }, [10]),
    call(12, "trader", "trader#1", { action: "Buy", size_pct: 5 }),
    art(13, "trader", "trader/proposal", 1, { action: "Buy", size_pct: 5 }, [12]),
    call(14, "risk.aggressive", "risk.aggressive#1", { stance: "aggressive", critique: "", size_pct: null, stop: null, target: null, cites: [], rebuttal: "" }),
    art(15, "risk.aggressive", "risk/aggressive", 1, { stance: "aggressive" }, [14]),
    call(16, "risk.conservative", "risk.conservative#1", { stance: "conservative", critique: "", size_pct: 3, stop: null, target: null, cites: [], rebuttal: "" }),
    art(17, "risk.conservative", "risk/conservative", 1, { stance: "conservative" }, [16]),
    call(18, "risk.neutral", "risk.neutral#1", { stance: "neutral", critique: "", size_pct: null, stop: null, target: null, cites: [], rebuttal: "" }),
    art(19, "risk.neutral", "risk/neutral", 1, { stance: "neutral" }, [18]),
    call(20, "trader", "trader#2", { action: "Buy", size_pct: 3 }),
    art(21, "trader", "trader/proposal", 2, { action: "Buy", size_pct: 3 }, [20]),
    gate(22, 0),
    call(23, "judge.evidence", "judge.evidence#1", { accept: true, target: "none", critique: "", cites: [] }),
    art(24, "judge.evidence", "judge.evidence/verdict", 1, { accept: true, target: "none" }, [23]),
    call(25, "judge.conservative", "judge.conservative#1", { accept: true, target: "none", critique: "", cites: [] }),
    art(26, "judge.conservative", "judge.conservative/verdict", 1, { accept: true, target: "none" }, [25]),
    art(27, "flow", "judges/status", 1, "accepted 2/3"),   // judges/status 的值是字符串
    call(28, "judge.aggressive", "judge.aggressive#1", { accept: true, target: "none", critique: "", cites: [] }),
    ev(99, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ];
  const model = p.fold(events, null, false);
  const turns = p.processTurns(model);

  const debate = p.debateRounds(turns);
  assert.equal(debate.length, 2);
  assert.equal(debate[1].bull.step, "bull#2");
  assert.equal(debate[1].bear.step, "bear#2");

  const risk = p.riskRounds(turns);
  assert.equal(risk.length, 1);
  assert.equal(JSON.stringify(risk[0].views.map((t) => t.actor)),
               JSON.stringify(["risk.aggressive", "risk.conservative", "risk.neutral"]));

  const judges = p.judgeRounds(model, turns);
  assert.equal(judges.length, 2);
  assert.equal(judges[0].status, "accepted 2/3");                  // 一轮以 judges/status 收尾
  // fold 在 vm 作用域里求值，数组跨 realm：结构一律用 JSON 比较
  assert.equal(JSON.stringify(judges[0].turns.map((t) => t.actor)),
               JSON.stringify(["judge.evidence", "judge.conservative"]));
  assert.equal(judges[1].status, null);                            // 没有汇总：被打断或进行中的一轮

  const revision = p.riskRevision(model, turns);                   // 风险辩论后交易员的修订
  assert.equal(revision.before.version, 1);
  assert.equal(revision.after.version, 2);
  assert.ok(revision.changes.join("").includes("仓位"));
});

test("修订对照：审计打回带检查项与去向；评审否决带反对票；随上游重做只作说明", () => {
  const p = loadProcess();
  const events = [
    ev(1, "run.started", "app", {}),
    call(2, "manager", "manager#1", { rating: "Buy", horizon_days: 20, summary: "第一版论断", points: [], invalidation: [] }),
    art(3, "manager", "manager/thesis", 1, { rating: "Buy", horizon_days: 20, summary: "第一版论断" }, [2]),
    call(4, "trader", "trader#1", { action: "Buy", entry: 100, stop: null, target: null, size_pct: 8 }),
    art(5, "trader", "trader/proposal", 1, { action: "Buy", entry: 100, stop: null, target: null, size_pct: 8 }, [4]),
    gate(6, 1, [{ check: "stop_required", subject: "proposal", message: "买入必须同时给出大于 0 的入场价与止损价", owner: "trader" }]),
    route(7, "trader", "审计发现 1 个问题"),
    call(8, "trader", "trader#2", { action: "Buy", entry: 100, stop: 95, target: 112, size_pct: 8 }),
    art(9, "trader", "trader/proposal", 2, { action: "Buy", entry: 100, stop: 95, target: 112, size_pct: 8 }, [8]),
    gate(10, 0),
    call(11, "judge.evidence", "judge.evidence#1", { accept: false, target: "thesis", critique: "论点太笼统", cites: [] }),
    art(12, "judge.evidence", "judge.evidence/verdict", 1, { accept: false, target: "thesis", critique: "论点太笼统" }, [11]),
    call(13, "judge.conservative", "judge.conservative#1", { accept: false, target: "thesis", critique: "失效条件不足", cites: [] }),
    art(14, "judge.conservative", "judge.conservative/verdict", 1, { accept: false, target: "thesis", critique: "失效条件不足" }, [13]),
    route(15, "manager", "评审否决论点"),
    call(16, "manager", "manager#2", { rating: "Buy", horizon_days: 20, summary: "第二版论断", points: [], invalidation: [] }),
    art(17, "manager", "manager/thesis", 2, { rating: "Buy", horizon_days: 20, summary: "第二版论断" }, [16]),
    call(18, "trader", "trader#3", { action: "Buy", entry: 100, stop: 95, target: 112, size_pct: 8 }),
    art(19, "trader", "trader/proposal", 3, { action: "Buy", entry: 100, stop: 95, target: 112, size_pct: 8 }, [18]),
    gate(20, 0),
    ev(21, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ];
  const model = p.fold(events, null, false);

  const proposal = p.revisionsOf(model, "trader/proposal", p.PROPOSAL_ROWS, "trader");
  assert.equal(proposal.columns.length, 3);
  const stopRow = proposal.rows.find((r) => r.field === "stop");
  assert.equal(JSON.stringify(stopRow.cells), JSON.stringify([null, 95, 95]));
  assert.equal(JSON.stringify(stopRow.changed), JSON.stringify([false, true, false]));   // 改动的格子标出
  assert.equal(proposal.back.length, 2);
  const auditBack = proposal.back.find((b) => b.source === "audit");
  assert.equal(auditBack.column.version, 1);
  assert.equal(auditBack.gate.data.detail[0].check, "stop_required");
  assert.equal(auditBack.route.data.to, "trader");
  assert.ok(auditBack.rewritten);
  const downstream = proposal.back.find((b) => b.source === "downstream");               // 论点被否决后随流程重做
  assert.equal(downstream.column.version, 2);
  assert.equal(downstream.route.data.to, "manager");

  const thesis = p.revisionsOf(model, "manager/thesis", p.THESIS_ROWS, "manager");
  assert.equal(thesis.columns.length, 2);
  assert.equal(thesis.back.length, 1);
  assert.equal(thesis.back[0].source, "judges");
  assert.equal(thesis.back[0].dissents.length, 2);                                       // 两位评审的反对票
  assert.equal(thesis.back[0].dissents[0].lens, "evidence");
  assert.equal(thesis.back[0].dissents[0].verdict.critique, "论点太笼统");
  assert.equal(thesis.back[0].route.data.to, "manager");
});

test("修订对照：主动修订（风险辩论后）不标被打回；最后一版被否而未重做也写明", () => {
  const p = loadProcess();
  const active = p.fold([
    ev(1, "run.started", "app", {}),
    call(2, "trader", "trader#1", { action: "Buy", size_pct: 8 }),
    art(3, "trader", "trader/proposal", 1, { action: "Buy", size_pct: 8 }, [2]),
    call(4, "risk.conservative", "risk.conservative#1", { stance: "conservative", critique: "", size_pct: 3, stop: null, target: null, cites: [], rebuttal: "" }),
    art(5, "risk.conservative", "risk/conservative", 1, { stance: "conservative" }, [4]),
    call(6, "trader", "trader#2", { action: "Buy", size_pct: 3 }),
    art(7, "trader", "trader/proposal", 2, { action: "Buy", size_pct: 3 }, [6]),
  ], null, false);
  const noBack = p.revisionsOf(active, "trader/proposal", p.PROPOSAL_ROWS, "trader");
  assert.equal(noBack.back.length, 0);                          // 主动修订：有对照表，但没有“被打回”

  const halted = p.fold([
    ev(1, "run.started", "app", {}),
    call(2, "trader", "trader#1", { action: "Buy", stop: 1 }),
    art(3, "trader", "trader/proposal", 1, { action: "Buy", stop: 1 }, [2]),
    gate(4, 1, [{ check: "stop_side", subject: "proposal", message: "方向反了", owner: "trader" }]),
    route(5, "trader", "审计发现 1 个问题"),
    call(6, "trader", "trader#2", { action: "Buy", stop: 2 }),
    art(7, "trader", "trader/proposal", 2, { action: "Buy", stop: 2 }, [6]),
    gate(8, 1, [{ check: "min_reward_risk", subject: "proposal", message: "收益风险比不足", owner: "trader" }]),
    ev(9, "run.finished", "flow", { status: "review", reason: "audit_failed" }),
  ], null, false);
  const rev = p.revisionsOf(halted, "trader/proposal", p.PROPOSAL_ROWS, "trader");
  const last = rev.back.find((b) => b.column.version === 2);    // 最后一版被否，但没有再重做
  assert.equal(last.source, "audit");
  assert.equal(last.rewritten, false);
});

test("fieldText：null 是“未给出”不是 0；价位带货币、仓位带百分号", () => {
  const p = loadProcess();
  assert.equal(p.fieldText("stop", null, "USD"), "—");
  assert.equal(p.fieldText("entry", 230.85, "USD"), "230.85 USD");
  assert.equal(p.fieldText("size_pct", 3, ""), "3%");
  assert.equal(p.fieldText("action", "Buy", ""), "Buy");
});
