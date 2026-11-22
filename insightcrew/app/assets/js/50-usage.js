/* 50-usage.js —— 用量与耗时页签（BLUEPRINT 20.10，WP49）：按角色的用量表、对上限的三条进度条、
   轨迹瀑布（一次调用一条横杠，并行分析师的横杠互相重叠；续跑与取消是竖线记号）。
   表与进度条从 fold 的 usage 画；瀑布从 fold 的 spans 画；记号从轨迹里私推出（fold 不收这两行）。 */

const NO_TRACE_NOTE = "这次运行没有记录耗时轨迹（版本 2.3 之前的运行与场景运行不记录），耗时一列与调用瀑布图因此省略。";
const INTERRUPTED_NOTE = "被中断的调用不记用量：用量只在调用返回后写进日志，中断时进行中的那一次不在任何数字里。";

const costText = (row) => row.unpriced === row.calls ? "未定价"
  : num(row.cost, 6) + (row.unpriced ? "（另有未定价）" : "");

function usageRoleName(actor) {                       // 角色名：泳道表优先，组名次之，最后原名
  const lane = LANES.find(([key]) => key === actor);
  if (lane) return lane[1];
  return GROUP_NAME[actor.split(".")[0]] || actor;
}

function msText(ms) {                                 // 毫秒 → 人读的时长；null 画成"—"
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return Math.round(ms) + " ms";
  if (ms < 60000) return (Math.round(ms / 100) / 10) + " 秒";
  return Math.floor(ms / 60000) + " 分 " + Math.round((ms % 60000) / 1000) + " 秒";
}

/* ---------- 按角色的用量表 ---------- */

function usageTable(model, withMs) {
  /* 每个角色一行（不是角色组）：调用、请求、输入与输出 token、token 合计与上限、成本（或未定价），
     有轨迹时再加耗时一列。角色组的 token 上限该组每个角色都适用，逐行照示。 */
  const ceilings = (model.started || {}).ceilings || {}, roleCaps = ceilings.roles || {};
  const header = ["角色", "调用", "请求", "输入 token", "输出 token", "token 合计", "token 上限", "成本"];
  const numeric = [1, 2, 3, 4, 5, 6];
  if (withMs) { header.push("耗时"); numeric.push(8); }
  const rows = Object.entries(model.usage.roles).map(([actor, row]) => {
    const cells = [usageRoleName(actor), count(row.calls), count(row.requests), count(row.input),
                   count(row.output), count(row.tokens),
                   roleCaps[actor.split(".")[0]] ? count(roleCaps[actor.split(".")[0]]) : "—",
                   costText(row)];
    if (withMs) cells.push(msText(row.ms || null));
    return cells;
  });
  const total = model.usage.total;
  const sum = ["合计", count(total.calls), count(total.requests), count(total.input), count(total.output),
               count(total.tokens), ceilings.run_tokens ? count(ceilings.run_tokens) : "不设上限",
               costText(total)];
  if (withMs) sum.push(msText(total.ms || null));
  sum.cls = "total";
  return table(header, [...rows, sum], numeric);
}

/* ---------- 与上限的比较 ---------- */

function limitStats(model, seconds) {
  /* 合计对三条上限：token 对运行上限、请求对 max_requests、秒对 max_seconds。
     秒是轨迹覆盖的墙钟时长（首行 role.started 到最后一条轨迹），并行调用不会重复计入。 */
  const ceilings = (model.started || {}).ceilings || {};
  const limits = (model.started || {}).limits || {};
  const total = model.usage.total;
  return [
    { key: "tokens", label: "token 用量", used: total.tokens, limit: ceilings.run_tokens || null },
    { key: "requests", label: "模型请求", used: total.requests, limit: limits.max_requests || null },
    { key: "seconds", label: "用时（墙钟）", used: seconds, limit: limits.max_seconds || null },
  ];
}

function limitBarsEl(model, seconds) {
  return el("div", { class: "limits" }, limitStats(model, seconds).map((stat) => {
    const hasLimit = stat.limit !== null && stat.limit !== undefined && stat.limit > 0;
    const usedText = stat.used === null || stat.used === undefined ? "未测量"
      : stat.key === "seconds" ? num(stat.used, 1) + " 秒" : count(stat.used);
    const txt = usedText + " / " + (hasLimit ? (stat.key === "seconds" ? count(stat.limit) + " 秒" : count(stat.limit)) : "不设上限")
      + (hasLimit && stat.used !== null && stat.used !== undefined
         ? "（" + num(100 * stat.used / stat.limit, 1) + "%）" : "");
    const meter = el("i");
    if (hasLimit && stat.used !== null && stat.used !== undefined)
      meter.style.width = Math.min(100, 100 * stat.used / stat.limit) + "%";
    return el("div", { class: "limit-row" },
      el("span", { class: "lab" }, stat.label),
      el("span", { class: "meter" + (hasLimit && stat.used > stat.limit ? " over" : "") }, meter),
      el("span", { class: "val" }, txt));
  }));
}

/* ---------- 轨迹瀑布 ---------- */

function marksOf(trace) {                              // fold 只收 role.*，续跑与取消两行在这里私推
  return (trace || []).filter((line) => line && (line.kind === "run.resumed" || line.kind === "run.cancelled"))
    .map((line) => ({ kind: line.kind, ts: line.ts || "", after_seq: line.after_seq }));
}

function wallSeconds(spans, marks) {                   // 轨迹覆盖的墙钟秒数：最早与最晚时间戳之差
  let t0 = null, t1 = null;
  const see = (ts) => {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return;
    if (t0 === null || t < t0) t0 = t;
    if (t1 === null || t > t1) t1 = t;
  };
  for (const s of spans) { see(s.start); if (s.end) see(s.end); }
  for (const m of marks) see(m.ts);
  return t0 === null || t1 === null ? null : (t1 - t0) / 1000;
}

function waterfallLanes(spans, marks) {
  /* 每个角色一条泳道，一次调用一条横杠：left/width 是占整条时间轴的比例（0–1）。
     没收尾的跨度（进行中或被中断）画到轴的末端；续跑与取消的记号也在轴上，时间轴覆盖到它们。
     时间戳解析不了的跨度不画。 */
  const times = [];
  for (const s of spans) {
    const a = Date.parse(s.start), b = s.end ? Date.parse(s.end) : null;
    if (!Number.isNaN(a)) times.push(a);
    if (b !== null && !Number.isNaN(b)) times.push(b);
  }
  for (const m of marks || []) {
    const t = Date.parse(m.ts);
    if (!Number.isNaN(t)) times.push(t);
  }
  if (!times.length) return null;
  const t0 = Math.min(...times), t1 = Math.max(...times), axis = Math.max(t1 - t0, 1);
  const byRole = new Map();
  for (const s of spans) {
    const a = Date.parse(s.start);
    if (Number.isNaN(a)) continue;
    const b = s.end && !Number.isNaN(Date.parse(s.end)) ? Date.parse(s.end) : null;
    if (!byRole.has(s.role)) byRole.set(s.role, []);
    byRole.get(s.role).push({ left: (a - t0) / axis, width: ((b === null ? t1 : b) - a) / axis,
                              open: b === null, span: s });
  }
  const lanes = [...byRole.entries()].map(([role, bars]) => ({ role, bars: bars.sort((x, y) => x.left - y.left) }));
  lanes.sort((x, y) => Math.min(...x.bars.map((b) => b.left)) - Math.min(...y.bars.map((b) => b.left)));
  return { lanes, t0, t1, axis };
}

const clockText = (ms) => new Date(ms).toISOString().slice(11, 19);   // 轨迹是 UTC，轴上只标时分秒

function barTitle(bar) {
  const s = bar.span;
  const state = bar.open ? "未收尾（进行中或被中断）" : s.ok === false ? "失败" : "完成";
  return usageRoleName(s.role) + " · " + (s.step || "") + (s.model ? " · " + s.model : "")
    + " · " + msText(s.ms) + " · " + state;
}

function markTitle(mark) {
  return mark.kind === "run.resumed"
    ? "续跑（当时日志已有 " + (mark.after_seq === undefined ? "—" : mark.after_seq) + " 条事件）"
    : "在这里取消：运行未结束，可以续跑";
}

function waterfallEl(model, marks) {
  const data = waterfallLanes(model.spans, marks);
  if (!data) return el("p", { class: "note" }, NO_TRACE_NOTE);
  const { lanes, t0, t1, axis } = data;
  const rows = el("div", { class: "wf-rows" });
  for (const lane of lanes) {
    const track = el("div", { class: "wf-track" });
    for (const b of lane.bars) {
      const bar = el("i", { class: "wf-bar" + (b.open ? " open" : b.span.ok === false ? " bad" : ""),
                            title: barTitle(b) },
        el("b", null, b.open ? "…" : msText(b.span.ms)));   // 杠内写时长（原型）；太窄时 CSS 截断
      bar.style.left = (b.left * 100) + "%";
      bar.style.width = Math.max(b.width * 100, 0.7) + "%";
      track.append(bar);
    }
    rows.append(el("div", { class: "wf-row" },
      el("div", { class: "wf-name" },
        el("strong", null, usageRoleName(lane.role)),
        el("small", null, (lane.bars[0].span.step || ""))), track));
  }
  const overlay = el("div", { class: "wf-marks" });
  for (const mark of marks) {
    const t = Date.parse(mark.ts);
    if (Number.isNaN(t)) continue;
    const at = (t - t0) / axis * 100;
    if (at < 0 || at > 100) continue;
    const line = el("i", { class: "wf-mark " + (mark.kind === "run.resumed" ? "resumed" : "cancelled"),
                           title: markTitle(mark) }, el("b", null, mark.kind === "run.resumed" ? "续跑" : "取消"));
    line.style.left = at + "%";
    overlay.append(line);
  }
  rows.append(overlay);
  const half = t0 + axis / 2;
  return el("div", { class: "wf" },
    el("div", { class: "wf-legend" },
      el("span", null, el("i", { class: "legend-bar" }), "执行时长"),
      el("span", null, el("i", { class: "legend-open" }), "未收尾（进行中或被中断）"),
      el("span", null, el("i", { class: "legend-mark" }), "续跑 / 取消的记号")),
    el("div", { class: "wf-axis" }, el("span", { class: "wf-name" }, ""),
      el("div", { class: "ticks" },
        el("span", { style: "left:0%" }, clockText(t0)),
        el("span", { style: "left:50%" }, clockText(half)),
        el("span", { style: "left:100%" }, clockText(t1)))),
    rows,
    el("p", { class: "note" },
      "每条横杠是一次模型调用，横向是时间：并行分析师的横杠互相重叠。紫色为完成，红色为失败，",
      "黄色虚线为未收尾（进行中或被中断）；竖线是续跑与取消的记号。悬停查看每条的开始与时长。"));
}

/* ---------- 页签 ---------- */

const costStat = (label, value, small) => el("div", { class: "cost-stat" }, el("span", null, label),
  el("strong", { class: "mono" }, value), el("small", null, small));

function renderUsage(model, box) {
  const kids = [el("h2", null, "用量与成本")];
  const total = model.usage.total;
  const hasTrace = model.spans.length > 0;
  const marks = marksOf(state.trace);
  const seconds = wallSeconds(model.spans, marks);

  if (!total.calls && !hasTrace) {
    fill(box, kids, el("div", { class: "muted" }, "还没有模型调用。"));
    return;
  }
  /* 顶上的四张汇总卡（WP56 照原型的 .cost-summary）：调用与请求、token、费用、历时 */
  kids.push(el("div", { class: "cost-summary" },
    costStat("模型调用", count(total.calls) + " / " + count(total.requests), "请求含纠正重试"),
    costStat("token 用量", count(total.tokens), "输入 " + count(total.input) + " + 输出 " + count(total.output)),
    costStat("已入账费用", costText(total), "USD；未定价的调用不进合计"),
    costStat("运行历时", seconds !== null ? num(seconds, 1) + " 秒" : "未测量", "取轨迹覆盖的范围")));
  if (!total.calls) kids.push(el("div", { class: "muted" }, "还没有已完成的模型调用；进行中的调用只出现在下面的瀑布里。"));
  else kids.push(usageTable(model, hasTrace));
  if (!hasTrace) kids.push(el("p", { class: "note" }, NO_TRACE_NOTE));

  kids.push(el("h3", null, "与上限的比较"), limitBarsEl(model, seconds));
  if (hasTrace) kids.push(el("h3", null, "调用瀑布"), waterfallEl(model, marks));

  const ceilings = (model.started || {}).ceilings || {};
  const notes = ["“请求”多于“调用”的部分是输出不合契约后的纠正重试。",
                 "成本上限：" + (ceilings.run_cost ? num(ceilings.run_cost, 6) : "不设上限") + "。",
                 INTERRUPTED_NOTE];
  const wall = model.board["run/wall_ms"];
  if (wall) notes.push("各段用时（毫秒）：" + Object.entries(wall)
    .map(([node, ms]) => (STAGE_NAME[node] || node) + " " + count(ms)).join("、") + "。");
  const exceeded = model.events.find((e) => e.type === "budget.exceeded");
  if (exceeded) notes.unshift("预算在第 " + exceeded.seq + " 条事件处超限（范围 " + exceeded.data.scope + "），此后不再发起模型调用。");
  kids.push(el("p", { class: "note" }, notes.join(" ")));
  fill(box, kids);
}

registerView({ id: "usage", title: "用量与耗时", render: renderUsage });
