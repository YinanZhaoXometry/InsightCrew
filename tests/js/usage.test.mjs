/* 用量与耗时视图（WP49，BLUEPRINT 20.10）：按角色的表、对上限的三条、瀑布与记号、无轨迹的替换行。
   本文件自带装载器：编号 20 以下的源文件加 50-usage.js，在同一函数作用域求值（页面的作用域规则），
   并注入一个最小的 state（renderUsage 只读 state.trace）与一个假 DOM（render 落在假节点上）。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import { ev } from "./helpers.mjs";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

/* 假 DOM：只实现 el/fill/table 用到的那几个方法，供 renderUsage 把界面搭出来再检查 */
class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.style = {};
                     this.className = ""; this.listeners = {}; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, fn) { (this.listeners[name] || (this.listeners[name] = [])).push(fn); }
}

function loadUsage(stateSnapshot) {
  const below = ["00-core.js", "05-labels.js", "10-model.js"].map((n) => readFileSync(path.join(SRC, n), "utf8")).join("\n");
  const mine = readFileSync(path.join(SRC, "50-usage.js"), "utf8");
  const state = JSON.stringify({ trace: [], ...(stateSnapshot || {}) });
  const wrapper = `(function () {\n"use strict";\n${below}\nconst state = ${state};\n${mine}\n`
    + "return { fold, renderUsage, usageTable, marksOf, wallSeconds, waterfallLanes, limitStats,"
    + " msText, usageRoleName, costText, VIEWS };\n})()";
  return vm.runInNewContext(wrapper, { document: { createElement: (t) => new FakeNode(t) }, Node: FakeNode });
}

const textOf = (node) => node.children.map((kid) => kid instanceof FakeNode ? textOf(kid) : String(kid)).join("");
const findAll = (node, cls) => {
  const found = [];
  const walk = (n) => {
    if (n instanceof FakeNode) {
      if (String(n.className).split(/\s+/).includes(cls)) found.push(n);
      for (const kid of n.children) walk(kid);
    }
  };
  walk(node);
  return found;
};
const trs = (box) => {
  const out = [];
  const walk = (n) => {
    if (n instanceof FakeNode) {
      if (n.tag === "tr") { out.push(n); return; }
      for (const kid of n.children) walk(kid);
    }
  };
  walk(box);
  return out;
};

/* 一次带轨迹的运行：两位并行分析师 + 交易员 + 一位评审 */
function richModel() {
  const { fold } = loadUsage();
  const events = [
    ev(1, "run.started", "app", { ceilings: { run_tokens: 200000, run_cost: null, roles: { judge: 30000 } },
                                  limits: { max_requests: 40, max_seconds: 900 } }),
    ev(2, "model.called", "technical", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 1, input_tokens: 1000, output_tokens: 200 }, cost: null }, "technical#1"),
    ev(3, "model.called", "fundamentals", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 2, input_tokens: 800, output_tokens: 150 }, cost: 0.004 }, "fundamentals#1"),
    ev(4, "model.called", "trader", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 1, input_tokens: 1200, output_tokens: 300 }, cost: 0.006 }, "trader#1"),
    ev(5, "model.called", "judge.evidence", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 1, input_tokens: 900, output_tokens: 120 }, cost: 0.002 }, "judge.evidence#1"),
  ];
  const trace = [
    { ts: "2026-11-20T00:00:01.000+00:00", kind: "role.started", step: "technical#1", role: "technical", model: "m" },
    { ts: "2026-11-20T00:00:01.500+00:00", kind: "role.started", step: "fundamentals#1", role: "fundamentals", model: "m" },
    { ts: "2026-11-20T00:00:03.000+00:00", kind: "role.finished", step: "technical#1", role: "technical",
      ms: 2000, ok: true, requests: 1, input_tokens: 1000, output_tokens: 200, tool_calls: 0 },
    { ts: "2026-11-20T00:00:03.500+00:00", kind: "role.finished", step: "fundamentals#1", role: "fundamentals",
      ms: 2000, ok: true, requests: 2, input_tokens: 800, output_tokens: 150, tool_calls: 0 },
    { ts: "2026-11-20T00:00:04.000+00:00", kind: "role.started", step: "trader#1", role: "trader", model: "m" },
    { ts: "2026-11-20T00:00:06.000+00:00", kind: "role.finished", step: "trader#1", role: "trader",
      ms: 2000, ok: true, requests: 1, input_tokens: 1200, output_tokens: 300, tool_calls: 0 },
    { ts: "2026-11-20T00:00:06.100+00:00", kind: "role.started", step: "judge.evidence#1", role: "judge.evidence", model: "m" },
    { ts: "2026-11-20T00:00:07.000+00:00", kind: "role.finished", step: "judge.evidence#1", role: "judge.evidence",
      ms: 900, ok: true, requests: 1, input_tokens: 900, output_tokens: 120, tool_calls: 0 },
  ];
  return { model: fold(events, trace, false), trace };
}

test("用量表：每个角色一行（不是角色组），有轨迹时带耗时列，合计在末行", () => {
  const scope = loadUsage();
  const { model } = richModel();
  const box = new FakeNode("section");
  scope.renderUsage(model, box);
  const rows = trs(box);
  assert.ok(textOf(rows[0]).includes("耗时"));     // 有轨迹：耗时列在
  const body = rows.slice(1, -1).map((r) => textOf(r));
  assert.equal(body.length, 4);                    // technical、fundamentals、trader、judge.evidence 各一行
  assert.ok(body[0].includes("技术分析师"));
  assert.ok(body[1].includes("基本面分析师"));
  assert.ok(body[3].includes("证据评审"));
  assert.ok(!body.join().includes("评审（三个视角）"));   // 不是按角色组
  assert.ok(body[0].includes("2 秒"));             // 耗时来自轨迹配对
  assert.ok(body[0].includes("未定价"));           // technical 的成本未定价
  assert.ok(body[1].includes("0.004"));            // fundamentals 有定价
  assert.ok(body[3].includes("30,000"));           // judge 组的 token 上限逐行照示
  const total = findAll(box, "total");
  assert.equal(total.length, 1);
  assert.ok(textOf(total[0]).includes("合计"));
  assert.ok(textOf(total[0]).includes("200,000")); // 运行 token 上限在合计行
  assert.ok(textOf(total[0]).includes("6.9 秒"));  // 合计耗时 = 各次调用之和（2000+2000+2000+900 ms）
});

test("无轨迹：耗时列与瀑布换成一行说明，表与上限条照画", () => {
  const scope = loadUsage();
  const events = [
    ev(1, "run.started", "app", { ceilings: { run_tokens: 200000 }, limits: { max_requests: 40, max_seconds: 900 } }),
    ev(2, "model.called", "technical", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 1, input_tokens: 1000, output_tokens: 200 }, cost: null }, "technical#1"),
  ];
  const model = scope.fold(events, null, false);
  assert.equal(model.spans.length, 0);
  const box = new FakeNode("section");
  scope.renderUsage(model, box);
  const text = textOf(box);
  assert.ok(text.includes("这次运行没有记录耗时轨迹"));
  const headings = [];
  const walkH = (n) => { if (n instanceof FakeNode) { if (n.tag === "h3") headings.push(textOf(n)); for (const k of n.children) walkH(k); } };
  walkH(box);
  assert.ok(!headings.includes("调用瀑布"));        // 没有瀑布这一节（说明行里提到它不算）
  assert.equal(findAll(box, "wf").length, 0);      // 没有瀑布
  assert.ok(!textOf(trs(box)[0]).includes("耗时"));  // 表头没有耗时列
  assert.ok(text.includes("与上限的比较"));         // 上限条不受影响
  assert.ok(text.includes("1,000"));               // 表照画
});

test("瀑布：一次调用一条横杠，并行角色的横杠互相重叠，泳道按最早开始排序", () => {
  const scope = loadUsage();
  const spans = [
    { step: "technical#1", role: "technical", model: "m", start: "2026-11-20T00:00:01.000+00:00",
      end: "2026-11-20T00:00:03.000+00:00", ms: 2000, ok: true },
    { step: "fundamentals#1", role: "fundamentals", model: "m", start: "2026-11-20T00:00:01.500+00:00",
      end: "2026-11-20T00:00:02.500+00:00", ms: 1000, ok: true },
    { step: "bear#1", role: "bear", model: "m", start: "2026-11-20T00:00:03.500+00:00",
      end: "2026-11-20T00:00:04.000+00:00", ms: 500, ok: true },
    { step: "bull#1", role: "bull", model: "m", start: "2026-11-20T00:00:03.800+00:00",
      end: null, ms: null, ok: null },             // 未收尾：进行中或被中断
  ];
  const data = scope.waterfallLanes(spans);
  // fold 在 vm 作用域里求值，数组跨 realm：结构一律用 JSON 比较（同 model.test.mjs 的约定）
  assert.equal(JSON.stringify(data.lanes.map((lane) => lane.role)),
               JSON.stringify(["technical", "fundamentals", "bear", "bull"]));
  const tech = data.lanes[0].bars[0], funda = data.lanes[1].bars[0], bull = data.lanes[3].bars[0];
  assert.ok(funda.left > tech.left && funda.left + funda.width < tech.left + tech.width);  // 重叠
  assert.equal(bull.open, true);                   // 未收尾的画到轴末端
  assert.ok(bull.left + bull.width > 0.99);
});

test("记号：fold 不收的两行在这里私推；墙钟秒数取轨迹覆盖的范围", () => {
  const scope = loadUsage();
  const trace = [
    { ts: "2026-11-20T00:00:01.000+00:00", kind: "role.started", step: "a", role: "technical", model: "m" },
    { ts: "2026-11-20T00:00:03.000+00:00", kind: "role.finished", step: "a", role: "technical", ms: 2000, ok: true },
    { ts: "2026-11-20T00:00:04.000+00:00", kind: "run.resumed", after_seq: 12 },
    { ts: "2026-11-20T00:00:05.000+00:00", kind: "run.cancelled" },
    { ts: "2026-11-20T00:00:05.500+00:00", kind: "role.started", step: "b", role: "bull", model: "m" },
  ];
  const model = scope.fold([ev(1, "run.started", "app", {})], trace, false);
  const marks = scope.marksOf(trace);
  assert.equal(JSON.stringify(marks.map((m) => m.kind)), JSON.stringify(["run.resumed", "run.cancelled"]));
  assert.equal(marks[0].after_seq, 12);
  const spans = model.spans.filter((s) => s.end !== null);
  assert.equal(scope.wallSeconds(spans, marks), 4);   // 最早 00:00:01，最晚记号 00:00:05
});

test("上限条：token 对运行上限、请求对 max_requests、秒对 max_seconds", () => {
  const scope = loadUsage();
  const { model } = richModel();
  const stats = scope.limitStats(model, 6);
  assert.equal(JSON.stringify(stats.map((s) => s.key)), JSON.stringify(["tokens", "requests", "seconds"]));
  assert.equal(stats[0].used, 4670);               // 四次调用的 token 合计
  assert.equal(stats[0].limit, 200000);
  assert.equal(stats[1].used, 5);                  // 请求含一次纠正重试（1+2+1+1）
  assert.equal(stats[1].limit, 40);
  assert.equal(stats[2].used, 6);
  assert.equal(stats[2].limit, 900);
  const none = scope.limitStats(scope.fold([ev(1, "run.started", "app", {})], null, false), null);
  assert.equal(JSON.stringify(none.map((s) => s.limit)), JSON.stringify([null, null, null]));   // 旧运行可能没有这些数字
  assert.equal(none[2].used, null);                // 没有轨迹：秒未测量
});

test("已中断的运行：表照画，瀑布里有未收尾的横杠与取消记号，附注说明用量不记", () => {
  const trace = [
    { ts: "2026-11-20T00:00:01.000+00:00", kind: "role.started", step: "technical#1", role: "technical", model: "m" },
    { ts: "2026-11-20T00:00:03.000+00:00", kind: "role.finished", step: "technical#1", role: "technical",
      ms: 2000, ok: true, requests: 1, input_tokens: 1000, output_tokens: 200, tool_calls: 0 },
    { ts: "2026-11-20T00:00:04.000+00:00", kind: "role.started", step: "bull#1", role: "bull", model: "m" },
    { ts: "2026-11-20T00:00:06.000+00:00", kind: "run.cancelled" },
  ];
  const scope = loadUsage({ trace });
  const events = [
    ev(1, "run.started", "app", { ceilings: { run_tokens: 200000 }, limits: { max_requests: 40, max_seconds: 900 } }),
    ev(2, "model.called", "technical", { model: "m", prompt: "", messages: [], output: {},
      usage: { requests: 1, input_tokens: 1000, output_tokens: 200 }, cost: null }, "technical#1"),
  ];                                                // bull 的调用没有返回：日志里没有它的 model.called
  const model = scope.fold(events, trace, false);
  assert.equal(model.status, "interrupted");
  const box = new FakeNode("section");
  scope.renderUsage(model, box);
  const text = textOf(box);
  assert.ok(text.includes("调用瀑布"));
  const bars = findAll(box, "wf-bar");
  assert.equal(bars.length, 2);
  assert.ok(bars[1].className.includes("open"));
  assert.equal(findAll(box, "wf-mark").length, 1);
  assert.ok(findAll(box, "wf-mark")[0].className.includes("cancelled"));
  assert.ok(text.includes("被中断的调用不记用量"));
  assert.ok(!trs(box).slice(1).some((r) => textOf(r).includes("多方研究员")));   // 未返回的调用不进用量表
  assert.ok(findAll(box, "wf-name").some((n) => textOf(n) === "多方研究员"));    // 但瀑布里画得出它
});

test("进行中的运行：还没有完成的调用时说明缘由，瀑布照画", () => {
  const trace = [
    { ts: "2026-11-20T00:00:01.000+00:00", kind: "role.started", step: "technical#1", role: "technical", model: "m" },
  ];
  const scope = loadUsage({ trace });
  const model = scope.fold([ev(1, "run.started", "app", {})], trace, true);
  const box = new FakeNode("section");
  scope.renderUsage(model, box);
  assert.ok(textOf(box).includes("还没有已完成的模型调用"));
  assert.equal(findAll(box, "wf-bar").length, 1);
});

test("视图注册：页签名“用量与耗时”，无调用且无轨迹时一行说明", () => {
  const scope = loadUsage();
  const registered = scope.VIEWS.find((v) => v.id === "usage");
  assert.ok(registered);
  assert.equal(registered.title, "用量与耗时");
  const box = new FakeNode("section");
  scope.renderUsage(scope.fold([], [], false), box);
  assert.ok(textOf(box).includes("还没有模型调用。"));
});
