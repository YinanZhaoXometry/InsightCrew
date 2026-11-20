/* 10-model.js —— fold(events, trace, live)：唯一的纯函数模型，每个视图都从它画（BLUEPRINT 20.9）。
   加载时不碰 document 或 window，Node 测试不用浏览器就能加载它。 */

const FACT_SOURCES = [["ledger/facts", "ledger"], ["fundamentals/facts", "fundamentals"],
                      ["tools/facts", "tool"], ["macro/facts", "macro"]];
const ITEM_SOURCES = [["disclosures/items", "disclosure"], ["news/items", "news"], ["macro/markets", "market"],
                      ["sentiment/posts", "post"], ["memory/lessons", "lesson"]];

function collectCites(value, found) {
  /* 走遍一个产物，收集角色引用的编号：键 fact/item/post 下的字符串，与 cites 数组里的字符串 */
  if (Array.isArray(value)) { for (const item of value) collectCites(item, found); return; }
  if (!value || typeof value !== "object") return;
  for (const [key, v] of Object.entries(value)) {
    if ((key === "fact" || key === "item" || key === "post") && typeof v === "string") { found.add(v); continue; }
    if (key === "cites" && Array.isArray(v)) {
      for (const id of v) if (typeof id === "string") found.add(id);
      continue;
    }
    collectCites(v, found);
  }
}

function spansOf(trace) {
  /* 轨迹 → 跨度：role.started 开一段，role.finished/role.failed 收一段；没收尾的在飞行中或被打断 */
  const spans = [], open = new Map();
  for (const line of trace) {
    if (!line || !line.kind) continue;
    const key = (line.step || "") + "|" + (line.role || "");
    if (line.kind === "role.started") {
      if (!open.has(key)) open.set(key, []);
      open.get(key).push({ step: line.step || "", role: line.role || "", model: line.model || "",
                           start: line.ts || "", end: null, ms: null, ok: null });
    } else if (line.kind === "role.finished" || line.kind === "role.failed") {
      const span = (open.get(key) || []).shift();
      if (!span) continue;                        // 只有收尾没有开始：轨迹不完整，忽略
      span.end = line.ts || "";
      span.ms = line.ms === undefined ? null : line.ms;
      span.ok = line.kind === "role.finished" ? line.ok !== false : false;
      spans.push(span);
    }
  }
  for (const queue of open.values()) spans.push(...queue);
  return spans;
}

function roundsOf(proposals, gates, routes) {
  /* 审计的历史：方案的每一版，配上检查它的那次审计闸门、它发现的问题、以及之后流向了哪一段 */
  const rounds = [];
  let gateAt = 0, routeAt = 0;
  for (const version of proposals) {
    while (gateAt < gates.length && gates[gateAt].data.gate !== "audit") gateAt += 1;
    while (gateAt < gates.length && gates[gateAt].seq < version.seq) gateAt += 1;
    const gate = gateAt < gates.length ? gates[gateAt] : null;
    if (gate) gateAt += 1;
    let route = null;
    if (gate && gate.data.findings > 0) {          // 只有被打回才有 route
      while (routeAt < routes.length && routes[routeAt].seq < gate.seq) routeAt += 1;
      route = routeAt < routes.length ? routes[routeAt] : null;
      if (route) routeAt += 1;
    }
    rounds.push({ seq: version.seq, version: version.version, value: version.value, by: version.by,
                  gate, findings: gate ? gate.data.detail || [] : [], route });
  }
  return rounds;
}

function fold(events, trace, live) {
  /* 事件（与可选的轨迹）折成一个对象：黑板、版本史、调用、闸门、路由、审计轮次、
     事实与条目、谁引用了谁、跨度、用量。一次渲染只折一次，每个视图从这里画。 */
  const board = {}, versions = {};
  const calls = [], gates = [], routes = [];
  let started = null, finished = null;
  for (const e of events) {
    const d = e.data || {};
    if (e.type === "run.started") started = d;
    else if (e.type === "run.finished") finished = d;
    else if (e.type === "artifact.written") {
      board[d.key] = d.value;
      (versions[d.key] || (versions[d.key] = [])).push(
        { seq: e.seq, version: d.version, value: d.value, by: e.actor || "", step: e.step || "", superseded: false });
    } else if (e.type === "model.called") {
      const usage = d.usage || {};
      calls.push({ seq: e.seq, step: e.step || "", actor: e.actor || "", model: d.model || "",
                   usage, cost: d.cost, output: d.output, tool_calls: d.tool_calls || 0,
                   retried: usage.requests > 1, ms: null });
    } else if (e.type === "gate.checked") gates.push(e);
    else if (e.type === "route") routes.push(e);
  }
  for (const list of Object.values(versions))
    for (let i = 0; i < list.length - 1; i += 1) list[i].superseded = true;

  const spans = spansOf(trace || []);
  const closed = new Map();                        // step|role → 已收尾的跨度，按收尾顺序
  for (const span of spans) if (span.end !== null) {
    const key = span.step + "|" + span.role;
    if (!closed.has(key)) closed.set(key, []);
    closed.get(key).push(span);
  }
  const usage = { roles: {}, total: { calls: 0, requests: 0, input: 0, output: 0, tokens: 0, cost: 0, unpriced: 0, ms: 0 } };
  for (const call of calls) {                      // 每次调用配自己的跨度得耗时；按角色与合计累计用量
    const queue = closed.get(call.step + "|" + call.actor);
    const span = queue && queue.length ? queue.shift() : null;
    if (span) call.ms = span.ms;
    const row = usage.roles[call.actor] || (usage.roles[call.actor] =
      { calls: 0, requests: 0, input: 0, output: 0, tokens: 0, cost: 0, unpriced: 0, ms: 0 });
    for (const acc of [row, usage.total]) {
      acc.calls += 1;
      acc.requests += call.usage.requests || 0;
      acc.input += call.usage.input_tokens || 0;
      acc.output += call.usage.output_tokens || 0;
      acc.tokens += (call.usage.input_tokens || 0) + (call.usage.output_tokens || 0);
      if (call.cost === null || call.cost === undefined) acc.unpriced += 1; else acc.cost += call.cost;
      if (call.ms !== null) acc.ms += call.ms;
    }
  }

  const facts = {}, items = {}, citedBy = {};      // 页面不计算事实，只把事件里的归成字典
  for (const [key, kind] of FACT_SOURCES) for (const fact of board[key] || [])
    facts[fact.id] = Object.assign({ kind }, fact);
  for (const [key, kind] of ITEM_SOURCES) for (const item of board[key] || []) {
    const id = item.id || item.ticker || "";
    if (id) items[id] = Object.assign({ id, kind }, item);
  }
  for (const e of events) if (e.type === "artifact.written") {
    const found = new Set();
    collectCites(e.data.value, found);
    for (const id of found)
      (citedBy[id] || (citedBy[id] = [])).push({ actor: e.actor || "", key: e.data.key, seq: e.seq });
  }

  const status = finished ? (finished.status === "delivered" ? "delivered" : "review")
    : (live ? "running" : "interrupted");
  return { events, started, finished, status, board, versions, calls, gates, routes,
           rounds: roundsOf(versions["trader/proposal"] || [], gates, routes),
           facts, items, cited_by: citedBy, spans, usage };
}
