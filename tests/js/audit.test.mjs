/* 规则与审计页签（WP48，BLUEPRINT 20.10「Rules and audit」）的纯推导：
   规则表（公式只在已交付的运行出现）、审计轮次（发现、去向、修正它的那一版）、分层标签。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

function loadAudit() {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "45-audit.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\n`
    + "return { fold, auRuleRows, auRoundCards, auProposalLine, auFindingShape, LAYER_NAME, LAYER_TEXT };\n})()";
  return vm.runInNewContext(wrapper, {});
}

const ev = (seq, type, actor, data, step = "") =>
  ({ seq, ts: "2026-11-20T00:00:00Z", type, actor, step, data, caused_by: [] });

/** 一版方案、一次审计落账、一次闸门，加规则与上限 */
const V1 = { action: "Buy", entry: 100, stop: 99, target: 120, size_pct: 24, horizon_days: 10, cites: ["close"] };
const V2 = { action: "Buy", entry: 100, stop: 95, target: 120, size_pct: 1, horizon_days: 10, cites: ["close"] };
const FINDING = { check: "max_risk_per_trade_pct", layer: "L3", owner: "trader", subject: "proposal",
                  message: "单笔风险 2.4%，超过上限 1%", limit: 1, actual: 2.4 };
const LIMITS = [{ key: "max_risk_per_trade_pct", limit: 1.0,
                  formula: "(entry − stop) / entry × size_pct", desc: "止损被触发时的亏损，占权益的百分比" },
                { key: "min_history", limit: 60, formula: "已结算 K 线的根数", desc: "历史必须足够长" }];
const RULES = [FINDING_SPEC(), { key: "min_history", status: "pass", limit: 60, actual: 250, message: "历史足够" }];
function FINDING_SPEC() { return { key: "max_risk_per_trade_pct", status: "fail", limit: 1, actual: 2.4,
                                   message: "单笔风险 2.4%，超过上限 1%" }; }

/** 交付的运行：第一版被审计打回，第二版通过 */
function deliveredModel(fold) {
  return fold([
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: V1 }, "trader#1"),
    ev(3, "artifact.written", "audit", { key: "audit/findings", version: 1, value: [FINDING] }),
    ev(4, "gate.checked", "audit", { gate: "audit", findings: 1,
      detail: [{ check: FINDING.check, subject: FINDING.subject, message: FINDING.message, owner: FINDING.owner }] }),
    ev(5, "route", "flow", { to: "trader", why: "审计发现 1 个问题" }),
    ev(6, "artifact.written", "trader", { key: "trader/proposal", version: 2, value: V2 }, "trader#2"),
    ev(7, "artifact.written", "audit", { key: "audit/findings", version: 2, value: [] }),
    ev(8, "gate.checked", "audit", { gate: "audit", findings: 0, detail: [] }),
    ev(9, "artifact.written", "audit", { key: "rules/limits", version: 1, value: LIMITS }),
    ev(10, "artifact.written", "audit", { key: "audit/rules", version: 1, value: RULES }),
    ev(11, "artifact.written", "flow", { key: "run/outcome", version: 1,
      value: { status: "delivered", reason: "ok" } }),
    ev(12, "run.finished", "flow", { status: "delivered", reason: "ok" }),
  ], null, false);
}

test("auRuleRows：中文标签来自标签表；公式只在已交付的运行出现（与报告同一规矩）", () => {
  const { fold, auRuleRows } = loadAudit();
  const model = deliveredModel(fold);
  const delivered = auRuleRows(model, true);
  assert.equal(JSON.stringify(delivered.map((r) => r.key)),
               JSON.stringify(["max_risk_per_trade_pct", "min_history"]));
  assert.equal(delivered[0].label, "止损被触发时的亏损，占权益的百分比不得超过上限");   // RULE_LABEL 的中文
  assert.equal(delivered[0].formula, "(entry − stop) / entry × size_pct");  // 公式照录 rules/limits
  assert.equal(delivered[0].message, "单笔风险 2.4%，超过上限 1%");         // 规则的说明进页面
  assert.equal(delivered[1].status, "pass");
  const review = auRuleRows(model, false);
  assert.equal(review.every((r) => r.formula === ""), true);                // 未交付：公式一栏不出现
  assert.equal(review[0].label, delivered[0].label);                        // 其余各栏不受影响
});

test("auRoundCards：每一版的发现（层级来自 audit/findings）、去向与修正它的那一版", () => {
  const { fold, auRoundCards } = loadAudit();
  const cards = auRoundCards(deliveredModel(fold));
  assert.equal(cards.length, 2);
  const [first, second] = cards;
  assert.equal(first.version, 1);
  assert.equal(first.checked, true);
  assert.equal(first.pass, false);
  assert.equal(first.findings.length, 1);
  assert.equal(first.findings[0].layer, "L3");                              // gate.detail 里没有，来自 audit/findings
  assert.equal(first.findings[0].label, RULE_LABEL_IS());
  assert.equal(first.findings[0].ownerText, "交易员");
  assert.equal(first.findings[0].limit, 1);
  assert.equal(first.findings[0].actual, 2.4);
  assert.equal(first.routeTo, "trader");
  assert.equal(first.routeText.includes("打回"), true);
  assert.equal(first.routeText.includes("交易员"), true);
  assert.equal(first.fixedBy, "由第 2 版方案修正（事件 #6）");
  assert.equal(second.pass, true);
  assert.equal(second.findings.length, 0);
  assert.equal(second.finalPass, true);                                     // 最后一版且已交付
});

function RULE_LABEL_IS() { return "止损被触发时的亏损，占权益的百分比不得超过上限"; }

test("auRoundCards：没等到审计的一版与等待重写的一轮", () => {
  const { fold, auRoundCards } = loadAudit();
  const stalled = auRoundCards(fold([                      // 运行在审计之前结束（REVIEW）
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: V1 }, "trader#1"),
    ev(3, "run.finished", "flow", { status: "review", reason: "audit_failed" }),
  ], null, false));
  assert.equal(stalled.length, 1);
  assert.equal(stalled[0].checked, false);
  assert.equal(stalled[0].fixedBy.includes("未修正"), true);
  assert.equal(stalled[0].fixedBy.includes("多次打回后审计仍未通过"), true);   // 停机原因有中文标签

  const waiting = auRoundCards(fold([                      // 打回之后运行中断：等待重写
    ev(1, "run.started", "app", {}),
    ev(2, "artifact.written", "trader", { key: "trader/proposal", version: 1, value: V1 }, "trader#1"),
    ev(3, "artifact.written", "audit", { key: "audit/findings", version: 1, value: [FINDING] }),
    ev(4, "gate.checked", "audit", { gate: "audit", findings: 1, detail: [] }),
    ev(5, "route", "flow", { to: "trader", why: "审计发现 1 个问题" }),
  ], null, false));
  assert.equal(waiting.length, 1);
  assert.equal(waiting[0].pass, false);
  assert.equal(waiting[0].checked, true);
  assert.equal(waiting[0].findings.length, 1);             // detail 为空时回落到 audit/findings 产物
  assert.equal(waiting[0].fixedBy, "等待交易员重写……");
});

test("auProposalLine：null 是「未给出」，不是 0；价位带货币、仓位带百分号", () => {
  const { auProposalLine } = loadAudit();
  const line = auProposalLine(V2, "USD");
  for (const bit of ["操作 Buy", "入场 100 USD", "止损 95 USD", "目标 120 USD", "仓位 1%", "持有 10 个交易日"])
    assert.equal(line.includes(bit), true, bit);
  const hold = auProposalLine({ action: "Hold", entry: null, stop: null, target: null,
                                size_pct: 0, horizon_days: 5 }, "USD");
  assert.equal(hold.includes("入场 —"), true);
  assert.equal(hold.includes("仓位 0%"), true);
});

test("分层标签：L1 与 L2 在此点名（连同发现会携带的 L3），都有名字与说明", () => {
  const { LAYER_NAME, LAYER_TEXT } = loadAudit();
  for (const key of ["L1", "L2", "L3"]) {
    assert.equal(LAYER_NAME[key].startsWith(key), true);
    assert.equal(LAYER_TEXT[key].length > 0, true);
  }
  assert.equal(LAYER_NAME.L1.includes("上下文"), true);
  assert.equal(LAYER_NAME.L2.includes("数字与引用"), true);
});
