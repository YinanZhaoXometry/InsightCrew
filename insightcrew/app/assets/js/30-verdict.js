/* 30-verdict.js —— 结论卡与价格图（WP44 放到位；WP46 接手）。总在渲染，不进页签。 */

/* ---------- 结果 ---------- */

function judgmentText(judgment) {
  if (!judgment) return "—";
  if (judgment.startsWith("accepted")) return "评审接受（" + judgment.slice("accepted".length).trim() + "）。评审只能否决，不能证明方案正确。";
  if (judgment === "contested") return "评审相持：多数评审反对。";
  if (judgment.startsWith("not reviewed")) {
    const why = judgment.includes(":") ? judgment.slice(judgment.indexOf(":") + 1).trim() : "";
    return "未经评审" + (why ? "（" + (why === "judges disabled" ? "评审已关闭" : reasonText(why)) + "）" : "") + "。";
  }
  return judgment;
}

function confidence(outcome) {
  return el("ul", { class: "confidence" },
    el("li", null, el("b", null, "事实与规则"), outcome.facts_and_rules === "verified"
      ? "已核验：数字由代码计算并复算，规则由代码逐条检查。" : "未核验：审计没有确认当前版本。"),
    el("li", null, el("b", null, "模型判断"), judgmentText(outcome.judgment)));
}

function renderResult(model) {
  const board = model.board, outcome = board["run/outcome"], box = $("result");
  if (!outcome) {
    fill(box, el("h2", null, "结果"),
      el("div", { class: "muted" }, complete()
        ? "这次运行没有留下结果。" : "运行尚未结束。结果在最后一步写出。"));
    return;
  }
  if (outcome.status !== "delivered") {         // REVIEW：这一块不出现任何评级与价位
    const kids = [el("h2", null, "结果"), el("div", { class: "verdict review" }, "REVIEW：未给出评级"),
      el("p", null, "原因：" + reasonText(outcome.reason) + "。")];
    const started = model.started;
    if (outcome.reason === "model_unavailable" && started && started.mode === "scripted") {
      kids.push(el("p", { class: "muted" }, "没有可用的真实模型；脚本化模型只能回答场景里写好的内容。"
        + (state.meta ? "可以选的离线场景：" + state.meta.scenarios.map((s) => s.name).join("、") + "。" : "")));
    }
    kids.push(confidence(outcome));
    fill(box, kids);
    return;
  }
  const thesis = board["manager/thesis"] || {}, proposal = board["trader/proposal"] || {};
  const currency = (board["ledger/instrument"] || {}).currency || "";
  const level = (label, value) => el("div", { class: "level" }, el("b", null, value), el("span", null, label));
  fill(box, el("h2", null, "结果"),
    el("div", { class: "verdict" }, "评级 " + thesis.rating + " · 操作 " + proposal.action, el("small", null, currency)),
    el("div", { class: "levels" },
      level("入场价", num(proposal.entry)), level("止损价", num(proposal.stop)), level("目标价", num(proposal.target)),
      level("仓位（占权益）", num(proposal.size_pct) + "%"), level("持有（交易日）", proposal.horizon_days)),
    proposal.rationale ? el("p", { class: "muted" }, "方案说明：" + proposal.rationale) : null,
    confidence(outcome));
}

/* ---------- 价格图 ---------- */

function renderChart(model) {
  const box = $("chart"), bars = state.bars;
  if (!bars || !bars.length) {
    const why = state.live && !state.ended ? "准入通过后加载。"
      : "这次运行没有通过准入，或数据集里读不到这个代码，所以没有价格序列。";
    fill(box, el("h2", null, "收盘价（截至分析日）"), el("div", { class: "muted" }, why));
    return;
  }
  const board = model.board, outcome = board["run/outcome"];
  const proposal = outcome && outcome.status === "delivered" ? board["trader/proposal"] || {} : {};
  const levels = [["入场", proposal.entry, "#18794e"], ["止损", proposal.stop, "#b42318"], ["目标", proposal.target, "#96600c"]]
    .filter(([, value]) => value !== null && value !== undefined);
  const closes = bars.map((bar) => bar.close);
  const low = Math.min(...closes), high = Math.max(...closes);
  const bottom = Math.min(low, ...levels.map(([, v]) => v)), top = Math.max(high, ...levels.map(([, v]) => v));
  const W = 640, H = 250, L = 52, R = 78, T = 14, B = 26;                 // 以下只是把数值换成画布坐标
  const x = (i) => L + (W - L - R) * i / Math.max(1, closes.length - 1);
  const y = (v) => T + (H - T - B) * (top - v) / ((top - bottom) || 1);
  const chart = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "收盘价走势" });
  for (const value of [high, low]) {
    chart.append(svg("line", { x1: L, x2: W - R, y1: y(value), y2: y(value), stroke: "#e2e7ee" }),
      svg("text", { x: L - 6, y: y(value) + 4, "text-anchor": "end", "font-size": 11, fill: "#7a8496" }, num(value, 2)));
  }
  chart.append(svg("polyline", { fill: "none", stroke: "#3451d1", "stroke-width": 1.6, "stroke-linejoin": "round",
    points: closes.map((c, i) => x(i).toFixed(1) + "," + y(c).toFixed(1)).join(" ") }));
  chart.append(svg("circle", { cx: x(closes.length - 1), cy: y(closes[closes.length - 1]), r: 3, fill: "#3451d1" }));
  const tags = levels.map(([label, value, color]) => ({ label, value, color, at: y(value) })).sort((a, b) => a.at - b.at);
  for (let i = 1; i < tags.length; i++) tags[i].at = Math.max(tags[i].at, tags[i - 1].at + 13);   // 价位挨得近时把标签错开
  for (const tag of tags) {
    chart.append(svg("line", { x1: L, x2: W - R, y1: y(tag.value), y2: y(tag.value), stroke: tag.color, "stroke-dasharray": "5 4" }),
      svg("text", { x: W - R + 6, y: tag.at + 4, "font-size": 11, fill: tag.color }, tag.label + " " + num(tag.value, 2)));
  }
  chart.append(svg("text", { x: L, y: H - 8, "font-size": 11, fill: "#7a8496" }, bars[0].date),
    svg("text", { x: W - R, y: H - 8, "text-anchor": "end", "font-size": 11, fill: "#7a8496" }, bars[bars.length - 1].date));
  const currency = state.currency || (board["ledger/instrument"] || {}).currency || "";
  fill(box, el("h2", null, "收盘价（截至分析日，" + bars.length + " 根" + (currency ? "，" + currency : "") + "）"), chart,
    levels.length ? null : el("p", { class: "note" }, outcome && outcome.status !== "delivered"
      ? "没有交付方案，所以图上没有价位线。" : "方案交付后，图上会画出入场、止损与目标价。"));
}

registerView({ id: "verdict", title: "结论", always: true, target: "result",
               render: (model) => { renderResult(model); renderChart(model); } });
