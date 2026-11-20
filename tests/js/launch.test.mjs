/* 55-launch.js 的核心行为：通道勾选（macro 须与 news 同选）、表单 → 请求体、
   计划预览的行（对 20.8 第 8 条的形状宽容）、上次值的存取（BLUEPRINT 20.10「发起」）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

function loadViews() {
  const files = ["00-core.js", "05-labels.js", "10-model.js", "55-launch.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { channelChoice, formBody, planRows, readSaved, writeSaved, CHANNEL_ORDER };\n})()`;
  return vm.runInNewContext(wrapper, { document: { getElementById: () => undefined, querySelector: () => null } });
}

/** 跨 realm 的对象只比内容：键排序后比 JSON（页面自己的测试也这么做） */
function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortKeys(value[key])]));
  }
  return value;
}

test("channelChoice：macro 只能跟 news 一起选，顺序固定", () => {
  const { channelChoice } = loadViews();
  assert.equal(JSON.stringify(channelChoice({}).names), "[]");
  assert.equal(channelChoice({}).macroAllowed, false);
  assert.equal(JSON.stringify(channelChoice({ macro: true }).names), "[]");        // 没有 news：macro 的勾作废
  assert.equal(JSON.stringify(channelChoice({ news: true, macro: true, fundamentals: true }).names),
               JSON.stringify(["fundamentals", "news", "macro"]));
  assert.equal(channelChoice({ news: true }).macroAllowed, true);
});

test("formBody：不用场景时逐项给出（空轮数不给、没勾的不给），选了场景只给四项", () => {
  const { formBody } = loadViews();
  const same = (a, b) => assert.equal(JSON.stringify(sortKeys(a)), JSON.stringify(sortKeys(b)));
  const body = formBody({ scenario: "", ticker: " ACME ", asOf: "2026-09-30", judges: true, rounds: "2",
                          riskRounds: "1", tools: true, memory: false, portfolio: "held_acme",
                          channels: { fundamentals: true, macro: true } });
  same(body, { ticker: "ACME", as_of: "2026-09-30", judges: true, debate_rounds: 2, risk_rounds: 1,
               tools: true, portfolio: "held_acme", channels: ["fundamentals"] });  // macro 没有 news 陪着：去掉
  const sparse = formBody({ scenario: "", ticker: "", asOf: "", judges: false, rounds: "",
                            riskRounds: "", tools: false, memory: false, portfolio: "", channels: {} });
  same(sparse, { judges: false, channels: [] });
  const scenario = formBody({ scenario: "demo", ticker: "", asOf: "", judges: true, rounds: "3",
                              riskRounds: "", tools: true, memory: true, portfolio: "", channels: {} });
  same(scenario, { scenario: "demo" });                                             // 场景自带流程与通道
});

test("planRows：把 /api/plan 的回复折成预览要画的行；字段缺失时不崩", () => {
  const { planRows } = loadViews();
  assert.equal(planRows(null), null);
  assert.equal(planRows("x"), null);
  const rows = planRows({
    ticker: "ACME", as_of: "2026-09-30", mode: "scripted",
    stages: [{ key: "intake", roles: ["intake"] },
             { key: "technical", roles: ["technical"], parallel: true },
             { key: "fundamentals", roles: ["fundamentals"], parallel: true },
             { key: "manager", roles: ["manager"] }],
    calls: { typical: 12, ceiling: 120 },
    ceilings: { run_tokens: 200000, run_cost: null },
    channels: { fundamentals: { state: "available", reason: "" },
                news: { state: "unavailable", reason: "尚未接入数据源" },
                macro: { state: "off" } },
    portfolio: { state: "flat" } });
  assert.equal(rows.ticker, "ACME");
  assert.equal(rows.asOf, "2026-09-30");
  assert.equal(rows.stages.length, 4);
  assert.equal(rows.stages[1].parallel, true);
  assert.equal(rows.stages[0].parallel, false);
  assert.equal(rows.calls.typical, 12);
  assert.equal(rows.calls.ceiling, 120);
  assert.equal(rows.runTokens, 200000);
  assert.equal(rows.runCost, null);
  assert.equal(rows.channels.length, 3);
  assert.equal(rows.channels[1].name, "news");
  assert.equal(rows.channels[1].state, "unavailable");
  assert.equal(rows.channels[1].reason, "尚未接入数据源");
  assert.equal(rows.portfolio, "flat");
  const empty = planRows({});
  assert.equal(empty.stages.length, 0);
  assert.equal(empty.channels.length, 0);
  assert.equal(empty.portfolio, null);
  assert.equal(empty.runTokens, null);
  assert.equal(empty.calls.typical, null);
  const bare = planRows({ portfolio: "held" });                                       // 组合状态也可以是一个词
  assert.equal(bare.portfolio, "held");
});

test("readSaved / writeSaved：上次的值整份存取，坏了的存档当作没有", () => {
  const { readSaved, writeSaved } = loadViews();
  const backing = new Map();
  const store = {
    getItem: (key) => (backing.has(key) ? backing.get(key) : null),
    setItem: (key, value) => backing.set(key, value),
  };
  writeSaved(store, { ticker: "ACME", channels: { news: true, macro: true } });
  const saved = readSaved(store);
  assert.equal(saved.ticker, "ACME");
  assert.equal(saved.channels.news, true);
  assert.equal(saved.channels.macro, true);
  assert.equal(readSaved({ getItem: () => null, setItem: () => {} }).ticker, undefined);
  assert.equal(JSON.stringify(readSaved({ getItem: () => "{oops", setItem: () => {} })), "{}");
});
