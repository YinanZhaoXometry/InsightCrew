/* 30-verdict.js —— 结论卡与价格图（WP46，BLUEPRINT 20.10「结论」）。总在渲染，不进页签。
   四种状态都要工作：直播运行、完成的运行（交付 / REVIEW）、被中断的运行、静态 replay.html。
   文件上半是纯推导（进度、建议、字段、状态判定，Node 测试直接测），下半才是碰 DOM 的渲染。 */

/* ---------- 纯推导 ---------- */

function verdictStatusOf(model, live, ended) {
  /* 四态（20.10）：完成看 run.finished；直播页上事件流已收尾（end 已到）而日志没有
     run.finished，说明运行被取消或崩溃，是被中断，不是仍在跑；静态页没有 run.finished 同样是中断。 */
  if (model.finished) return model.finished.status === "delivered" ? "delivered" : "review";
  return live && !ended ? "running" : "interrupted";
}
const verdictStatus = (model) => verdictStatusOf(model, state.live, state.ended);

function verdictStageOf(e) {
  /* 一条事件属于流水线的哪一段：评审先看执行者（judge.* 不在 ACTOR_STAGE 里）；
     闸门看闸门名；产物先按键前缀（05-labels 的 KEY_STAGE），再按键的首段
     （technical/analysis → technical）；模型调用与失败看执行者。 */
  const actor = e.actor || "";
  if (actor.startsWith("judge.")) return "judges";
  if (e.type === "gate.checked") return e.data.gate || null;
  if (e.type === "artifact.written") {
    for (const [prefix, stage] of KEY_STAGE) if ((e.data.key || "").startsWith(prefix)) return stage;
    const head = (e.data.key || "").split("/")[0];
    return STAGE_NAME[head] ? head : null;
  }
  if (e.type === "model.called" || e.type === "model.failed") return ACTOR_STAGE[actor] || null;
  return null;
}

function verdictProgress(events) {
  /* 运行到了哪一步：最后一条能归段的事件所在的段，加上调用与事件计数。 */
  let stage = null, calls = 0;
  for (const e of events) {
    if (e.type === "model.called") calls += 1;
    const at = verdictStageOf(e);
    if (at && at !== "report") stage = at;
  }
  const line = stage
    ? "已进行到「" + (STAGE_NAME[stage] || stage) + "」段；模型调用 " + calls + " 次，事件 " + events.length + " 条。"
    : (events.length ? "已收到 " + events.length + " 条事件；模型调用 " + calls + " 次。" : "正在等待第一条事件。");
  return { stage, calls, line };
}

function verdictAdvice(reason) {
  /* REVIEW 的下一步（20.10 只点名这三种）；tab 是这条建议链去哪一页看。 */
  if (reason === "budget_exhausted")
    return { text: "token 预算已用尽；提高预算上限后重新发起，运行就能在预算内跑完。", tab: null };
  if (reason === "contested")
    return { text: "评审多数反对；读各评审的反对意见，再决定要不要调整后重新发起。", tab: "process" };
  if (reason === "model_unavailable")
    return { text: "模型不可用；等模型恢复后可以续跑这次运行。", tab: null };
  return null;
}

function verdictData(model) {
  /* 结论卡用到的全部黑板字段；缺键时为 null / 空串，渲染端各自兜底。 */
  const board = model.board;
  return {
    outcome: board["run/outcome"] || null,
    thesis: board["manager/thesis"] || null,
    proposal: board["trader/proposal"] || null,
    currency: (board["ledger/instrument"] || {}).currency || "",
  };
}

/* ---------- 两条信任行（20.10）：标签本身是链接，链去各自的页签 ---------- */

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

function tabLink(tab, label, title) {
  return el("button", { class: "tablink", type: "button", title: title || null,
                        on: { click: () => selectTab(tab) } }, label);
}

function trustBlock(outcome) {
  return el("ul", { class: "confidence" },
    el("li", null, tabLink("audit", "事实与规则", "数字与规则的核验在「规则与审计」页签"),
      outcome.facts_and_rules === "verified"
        ? "已核验：数字由代码计算并复算，规则由代码逐条检查。"
        : "未核验：审计没有确认当前版本。"),
    el("li", null, tabLink("process", "模型评审", "评审的逐条意见在「过程」页签"),
      judgmentText(outcome.judgment)));
}

/* ---------- 续跑（20.10：中断态给出续跑按钮；静态回放页不给需要服务器的控件） ---------- */

async function verdictResume() {
  const runId = state.runId;
  const note = $("resume-note");
  if (note) note.textContent = "正在请求续跑……";
  let answer;
  try {
    answer = await getJSON("/api/runs/" + encodeURIComponent(runId) + "/resume", { method: "POST" });
  } catch (_) {
    if (note) note.textContent = "续跑请求发不出去：与本地服务的连接不可用。";
    return;
  }
  if (answer.status === 202) { openRun(runId, false); return; }   // 重新接上这次运行的事件流
  if (note) note.textContent = answer.status === 404
    ? "本地服务没有续跑接口。"
    : "不能续跑：" + ((answer.data && (answer.data.reason || answer.data.detail)) || "本地服务拒绝，原因未知") + "。";
}

function resumeControl() {
  if (!state.live)
    return el("p", { class: "note" }, "静态回放页不能续跑；在实时页面或命令行可以续跑这次运行。");
  return el("p", { class: "note" },
    el("button", { class: "ctl", type: "button", on: { click: () => { void verdictResume(); } } }, "续跑这次运行"),
    " ", el("span", { id: "resume-note" }, ""));
}

/* ---------- 结果卡 ---------- */

function renderResult(model) {
  const box = $("result");
  const status = verdictStatus(model);
  const { outcome, thesis, proposal, currency } = verdictData(model);
  const progress = verdictProgress(model.events);

  if (status === "interrupted") {                    // 中断：到了哪一步；续跑按钮（静态页说明）
    fill(box, el("h2", null, "结果"),
      el("div", { class: "verdict review" }, "运行中断，未形成结论"),
      el("p", { class: "muted" }, progress.line),
      el("p", { class: "note" }, "这次运行没有正常结束；已完成的阶段仍可查看，已入账的模型调用都在日志里。"),
      resumeControl());
    return;
  }
  if (!outcome) {                                    // 运行中：知道了什么 + 结论形成中
    fill(box, el("h2", null, "结果"),
      el("div", { class: "verdict forming" }, "结论形成中"),
      el("p", { class: "muted" }, progress.line),
      el("p", { class: "note" }, complete()
        ? "这次运行没有留下结果。"
        : "运行尚未结束，结果在最后一步写出；每个数字由代码计算并复算。"));
    return;
  }
  if (outcome.status !== "delivered") {              // REVIEW：这一块不出现任何评级与价位
    const kids = [el("h2", null, "结果"),
      el("div", { class: "verdict review" }, "REVIEW：未给出评级"),
      el("p", null, "原因：" + reasonText(outcome.reason) + "。")];
    const advice = verdictAdvice(outcome.reason);
    if (advice) kids.push(el("p", { class: "note advice" }, "下一步：" + advice.text + " ",
      advice.tab ? tabLink(advice.tab, "查看", "这条建议所指的内容在这一页签") : null));
    const started = model.started;
    if (outcome.reason === "model_unavailable" && started && started.mode === "scripted") {
      kids.push(el("p", { class: "muted" }, "没有可用的真实模型；脚本化模型只能回答场景里写好的内容。"
        + (state.meta ? "可以选的离线场景：" + state.meta.scenarios.map((s) => s.name).join("、") + "。" : "")));
    }
    kids.push(trustBlock(outcome));
    fill(box, kids);
    return;
  }
  const the = thesis || {}, prop = proposal || {};   // 交付：结论在上，证据与失效条件在下
  const level = (label, value, cls) => el("div", { class: "level" + (cls ? " " + cls : "") }, el("b", null, value), el("span", null, label));
  const withCur = (label) => currency ? label + "（" + currency + "）" : label;
  const points = the.points || [], invalidation = the.invalidation || [];
  const side = /buy|overweight/i.test(the.rating || "") ? " long" : /sell|underweight/i.test(the.rating || "") ? " short" : "";
  fill(box, el("h2", null, "结果"),
    el("div", { class: "verdict" + side }, "评级 " + (the.rating || "—") + " · 操作 " + (prop.action || "—"),
      currency ? el("small", null, currency) : null),
    el("div", { class: "levels" },
      level(withCur("入场价"), num(prop.entry), "lv-entry"),
      level(withCur("止损价"), num(prop.stop), "lv-stop"),
      level(withCur("目标价"), num(prop.target), "lv-target"),
      level("仓位（占权益）", num(prop.size_pct) + "%"),
      level("持有（交易日）", prop.horizon_days === undefined || prop.horizon_days === null ? "—" : prop.horizon_days)),
    the.summary ? el("p", { class: "summary" }, the.summary) : null,
    points.length ? el("h3", { class: "v-h" }, "为什么（主管论点）") : null,
    points.length ? el("ul", { class: "points" }, points.map((point) => el("li", null,
      el("span", { class: "ptext" }, point.text), " ",
      ...(point.cites || []).map((id) => cite(id))))) : null,
    invalidation.length ? el("h3", { class: "v-h" }, "什么情况说明这个判断错了（失效条件）") : null,
    invalidation.length ? el("ul", { class: "invalidation" }, invalidation.map((text) => el("li", null, text))) : null,
    prop.rationale ? el("p", { class: "muted" }, "方案说明：" + prop.rationale) : null,
    trustBlock(outcome));
}

/* ---------- 价格图：今天的收盘与价位照旧，另标出分析日 ---------- */

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
  const levels = [["入场", proposal.entry, "var(--info)"], ["止损", proposal.stop, "var(--bad)"], ["目标", proposal.target, "var(--good)"]]
    .filter(([, value]) => value !== null && value !== undefined);
  const closes = bars.map((bar) => bar.close);
  const low = Math.min(...closes), high = Math.max(...closes);
  const bottom = Math.min(low, ...levels.map(([, v]) => v)), top = Math.max(high, ...levels.map(([, v]) => v));
  const W = 640, H = 250, L = 58, R = 108, T = 16, B = 30;                 // 以下只是把数值换成画布坐标
  const x = (i) => L + (W - L - R) * i / Math.max(1, closes.length - 1);
  const y = (v) => T + (H - T - B) * (top - v) / ((top - bottom) || 1);
  const chart = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "收盘价走势" });
  for (const value of [high, low]) {
    chart.append(svg("line", { x1: L, x2: W - R, y1: y(value), y2: y(value), class: "ch-grid" }),
      svg("text", { x: L - 6, y: y(value) + 4, "text-anchor": "end", "font-size": 14, class: "ch-label" }, num(value, 2)));
  }
  chart.append(svg("polygon", { class: "ch-area", points: x(0).toFixed(1) + "," + (H - B) + " "
    + closes.map((c, i) => x(i).toFixed(1) + "," + y(c).toFixed(1)).join(" ") + " " + x(closes.length - 1).toFixed(1) + "," + (H - B) }));
  chart.append(svg("polyline", { fill: "none", class: "ch-line", "stroke-width": 1.6, "stroke-linejoin": "round",
    points: closes.map((c, i) => x(i).toFixed(1) + "," + y(c).toFixed(1)).join(" ") }));
  chart.append(svg("circle", { cx: x(closes.length - 1), cy: y(closes[closes.length - 1]), r: 3, class: "ch-dot" }));
  const scope = board["request/scope"] || {}, request = (model.started || {}).request || {};
  const asOf = scope.as_of || request.as_of || "";                        // 分析日：最后一段收盘所在的那条竖线
  if (asOf) {
    const lastX = x(closes.length - 1);
    chart.append(svg("line", { x1: lastX, x2: lastX, y1: T, y2: H - B, class: "ch-asof",
                              "stroke-dasharray": "2 4", opacity: "0.9" }),
      svg("text", { x: lastX - 6, y: H - B - 6, "text-anchor": "end", "font-size": 14, class: "ch-label" }, "分析日 " + asOf));
  }
  const tags = levels.map(([label, value, color]) => ({ label, value, color, at: y(value) })).sort((a, b) => a.at - b.at);
  for (let i = 1; i < tags.length; i++) tags[i].at = Math.max(tags[i].at, tags[i - 1].at + 13);   // 价位挨得近时把标签错开
  for (const tag of tags) {
    chart.append(svg("line", { x1: L, x2: W - R, y1: y(tag.value), y2: y(tag.value), style: "stroke:" + tag.color, "stroke-dasharray": "5 4" }),
      svg("text", { x: W - R + 6, y: tag.at + 4, "font-size": 14, style: "fill:" + tag.color }, tag.label + " " + num(tag.value, 2)));
  }
  chart.append(svg("text", { x: L, y: H - 9, "font-size": 14, class: "ch-label" }, bars[0].date),
    svg("text", { x: W - R, y: H - 9, "text-anchor": "end", "font-size": 14, class: "ch-label" }, bars[bars.length - 1].date));
  const currency = state.currency || (board["ledger/instrument"] || {}).currency || "";
  fill(box, el("h2", null, "收盘价（截至分析日，" + bars.length + " 根" + (currency ? "，" + currency : "") + "）"), chart,
    levels.length ? null : el("p", { class: "note" }, outcome && outcome.status !== "delivered"
      ? "没有交付方案，所以图上没有价位线。" : "方案交付后，图上会画出入场、止损与目标价。"));
}

registerView({ id: "verdict", title: "结论", always: true, target: "result",
               render: (model) => { renderResult(model); renderChart(model); } });
