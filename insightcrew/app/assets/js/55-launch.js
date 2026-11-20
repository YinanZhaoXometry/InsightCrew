/* 55-launch.js —— 发起运行的表单、计划预览与运行历史（WP50 接手，BLUEPRINT 20.10「发起」）。
   表单分四组：标的与日期 / 流程 / 证据通道 / 组合；改动后刷新计划预览（POST /api/plan，
   服务端没有这个接口时整个预览收起）；上次填的值记在 localStorage；历史里可续跑的运行有续跑键。
   元素 ID 保持不变：f-scenario、f-ticker、f-date、f-judges、f-rounds、f-risk-rounds、
   f-tools、f-memory、f-portfolio、f-channels（通道由一个多选框改成五个复选框，容器仍用这个 ID）。 */

const CHANNELS = [["fundamentals", "年度报表事实"], ["disclosures", "窗口内的公告条目"],
                  ["news", "窗口内的新闻（含宏观的入口）"], ["sentiment", "社交帖子情感"],
                  ["macro", "宏观序列（需同时勾选 news）"]];
const CHANNEL_ORDER = CHANNELS.map(([name]) => name);
const PLAN_CHANNEL_WORD = { off: "关", available: "可用", unavailable: "不可用", empty: "窗口内没有内容" };
const STORE_KEY = "insightcrew.launch";

/* ---------- 纯函数（Node 测试直接调） ---------- */

function channelChoice(checked) {
  /* 勾了哪几条通道；服务端要求 macro 必须与 news 同选，这里先替它把不合规的勾去掉 */
  const names = CHANNEL_ORDER.filter((name) => checked[name] === true);
  return { names: checked.news === true ? names : names.filter((name) => name !== "macro"),
           macroAllowed: checked.news === true };
}

function formBody(v) {
  /* 表单当前的值 → 提交给 POST /api/runs 的请求体（与旧页相同的字段与缺省规则） */
  const body = {};
  if (v.scenario) body.scenario = v.scenario;
  if (v.ticker && v.ticker.trim()) body.ticker = v.ticker.trim();
  if (v.asOf) body.as_of = v.asOf;
  if (v.portfolio) body.portfolio = v.portfolio;
  if (!v.scenario) {
    body.judges = v.judges === true;
    if (v.rounds !== "" && v.rounds !== null && v.rounds !== undefined) body.debate_rounds = Number(v.rounds);
    if (v.riskRounds !== "" && v.riskRounds !== null && v.riskRounds !== undefined) body.risk_rounds = Number(v.riskRounds);
    if (v.tools === true) body.tools = true;
    if (v.memory === true) body.memory = true;
    body.channels = channelChoice(v.channels || {}).names;
  }
  return body;
}

function planRows(plan) {
  /* POST /api/plan 的回复 → 预览要画的行（20.8 第 8 条）。字段缺失时给空值，不让预览崩 */
  if (!plan || typeof plan !== "object") return null;
  const stages = (Array.isArray(plan.stages) ? plan.stages : []).map((s) => ({
    key: (s && s.key) || "", parallel: Boolean(s && s.parallel),
    roles: Array.isArray(s && s.roles) ? s.roles : [] }));
  const calls = plan.calls && typeof plan.calls === "object" ? plan.calls : {};
  const ceilings = plan.ceilings && typeof plan.ceilings === "object" ? plan.ceilings : {};
  const channels = plan.channels && typeof plan.channels === "object" && !Array.isArray(plan.channels)
    ? Object.entries(plan.channels).map(([name, info]) => ({ name,
        state: info && typeof info === "object" ? (info.state || "") : String(info || ""),
        reason: info && typeof info === "object" ? (info.reason || "") : "" }))
    : [];
  const portfolio = plan.portfolio === null || plan.portfolio === undefined ? null
    : typeof plan.portfolio === "object" ? (plan.portfolio.state || "") : String(plan.portfolio);
  return { ticker: plan.ticker || "", asOf: plan.as_of || "", mode: plan.mode || "", stages,
           calls: { typical: calls.typical !== undefined ? calls.typical : null,
                    ceiling: calls.ceiling !== undefined ? calls.ceiling : null },
           runTokens: ceilings.run_tokens !== undefined ? ceilings.run_tokens : plan.run_tokens !== undefined ? plan.run_tokens : null,
           runCost: ceilings.run_cost !== undefined ? ceilings.run_cost : plan.run_cost !== undefined ? plan.run_cost : null,
           channels, portfolio };
}

function readSaved(store) {
  try {
    const raw = store.getItem(STORE_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" ? value : {};
  } catch (_) { return {}; }
}

function writeSaved(store, values) {
  try { store.setItem(STORE_KEY, JSON.stringify(values)); } catch (_) { /* 隐私模式等存不进就算了 */ }
}

/* ---------- 实时表单（加载时把 20.9 留下的平铺表单重排成四组；静态页不进来） ---------- */

let planAvailable = true, planTimer = 0, planSeq = 0, startPending = false;
let metaApplied = false, savedApplied = false, foldedFor = "?";

function buildLaunch() {
  const host = $("launch");
  if (!host) return;
  const group = (title, ...kids) => el("fieldset", { class: "fgroup" }, el("legend", null, title), ...kids);
  const lab = (text, control, ...extra) => el("label", null, el("span", { class: "lab" }, text), control, ...extra);
  const form = el("form", { id: "form" },
    group("标的与日期",
      lab("离线场景", el("select", { id: "f-scenario" })),
      lab("代码", el("input", { id: "f-ticker", maxlength: "32", placeholder: "ACME / 0700.HK",
                                list: "tickers", autocomplete: "off" }),
          el("span", { class: "field-note", id: "ticker-note" }),
          el("span", { class: "field-error", id: "err-ticker" })),
      el("datalist", { id: "tickers" }),
      lab("分析日", el("input", { id: "f-date", type: "date" }),
          el("span", { class: "field-error", id: "err-date" }))),
    group("流程",
      el("label", { class: "inline" }, el("input", { id: "f-judges", type: "checkbox" }), " 模型评审"),
      lab("辩论轮数", el("input", { id: "f-rounds", type: "number", min: "0", max: "10" })),
      lab("风险辩论轮数", el("input", { id: "f-risk-rounds", type: "number", min: "0", max: "10" })),
      el("label", { class: "inline" }, el("input", { id: "f-tools", type: "checkbox" }), " 分析工具"),
      el("label", { class: "inline" }, el("input", { id: "f-memory", type: "checkbox" }), " 记忆")),
    group("证据通道",
      el("div", { id: "f-channels", class: "chan" },
        CHANNELS.map(([name, line]) => el("label", { class: "chan-line" },
          el("input", { type: "checkbox", id: "fc-" + name, value: name }),
          el("b", null, name), el("span", { class: "muted" }, line))),
        el("span", { class: "field-error", id: "err-channels" }))),
    group("组合",
      lab("组合文件", el("select", { id: "f-portfolio" }),
          el("span", { class: "field-error", id: "err-portfolio" }))),
    el("button", { class: "primary", type: "submit", id: "btn-start" }, "开始"));
  fill(host, el("details", { id: "launch-fold", open: true },
    el("summary", null, el("span", { class: "fold-title" }, "发起一次运行"),
      el("span", { class: "muted", id: "fold-hint" })),
    el("div", { class: "launch-grid" }, form, el("div", { class: "plan", id: "plan", hidden: true })),
    el("p", { class: "note", id: "mode-note" }),
    el("p", { class: "note", id: "scenario-note", hidden: true }),
    el("p", { class: "error", id: "form-error", hidden: true }),
    el("details", { id: "history" },
      el("summary", null, "过去的运行"),
      el("div", { id: "history-body", class: "muted" }, "（加载中）"))));
  form.addEventListener("input", () => { remember(); schedulePlan(); clearFieldErrors(); });
  form.addEventListener("change", () => {
    remember(); updateMacroGate(); updateTickerHint(); schedulePlan(); clearFieldErrors();
  });
  $("fc-news").addEventListener("change", updateMacroGate);
}

function snapshot() {
  return { scenario: $("f-scenario").value, ticker: $("f-ticker").value, asOf: $("f-date").value,
           judges: $("f-judges").checked, rounds: $("f-rounds").value, riskRounds: $("f-risk-rounds").value,
           tools: $("f-tools").checked, memory: $("f-memory").checked, portfolio: $("f-portfolio").value,
           channels: Object.fromEntries(CHANNEL_ORDER.map((name) => [name, Boolean($("fc-" + name).checked)])) };
}

function remember() { writeSaved(localStorage, snapshot()); }

function setChannelsDisabled(disabled) {
  for (const name of CHANNEL_ORDER) {
    const box = $("fc-" + name);
    if (box) { box.disabled = disabled || (name === "macro" && !$("fc-news").checked); }
  }
}

function updateMacroGate() {
  const news = $("fc-news"), macro = $("fc-macro");
  if (!news || !macro) return;
  macro.disabled = news.disabled || !news.checked;      /* 服务端要求：macro 只能跟 news 一起选 */
  if (!news.checked && macro.checked) macro.checked = false;
}

function instrumentOf(ticker) {
  const wanted = (ticker || "").toUpperCase();
  return ((state.meta && state.meta.instruments) || []).find((i) => i.ticker.toUpperCase() === wanted) || null;
}

function updateTickerHint() {
  const note = $("ticker-note");
  if (!note) return;
  const instrument = instrumentOf($("f-ticker").value);
  if (!instrument) { note.textContent = ""; $("f-date").removeAttribute("max"); return; }
  note.textContent = instrument.last_bar
    ? "最后已收盘 K 线：" + instrument.last_bar + (instrument.currency ? " · " + instrument.currency : "")
    : "这个代码在数据集里没有已收盘的行情";
  if (instrument.last_bar) $("f-date").setAttribute("max", instrument.last_bar);   /* 分析日不能晚于它 */
}

function applyMetaDefaults() {
  const defaults = (state.meta && state.meta.defaults) || {};
  if (defaults.risk_rounds !== undefined) $("f-risk-rounds").value = defaults.risk_rounds;
  if (defaults.tools !== undefined) $("f-tools").checked = Boolean(defaults.tools);
  if (defaults.memory !== undefined) $("f-memory").checked = Boolean(defaults.memory);
  if (Array.isArray(defaults.channels))
    for (const name of defaults.channels) { const box = $("fc-" + name); if (box) box.checked = true; }
  fill($("f-portfolio"), el("option", { value: "" }, "无"),
    ((state.meta && state.meta.portfolios) || []).map((name) => el("option", { value: name }, name)));
  updateMacroGate();
}

function applySaved() {
  const saved = readSaved(localStorage);
  if (saved.scenario !== undefined) {
    const select = $("f-scenario");
    if ([...select.options].some((option) => option.value === saved.scenario)) select.value = saved.scenario;
  }
  if (saved.ticker !== undefined) $("f-ticker").value = saved.ticker;
  if (saved.asOf !== undefined) $("f-date").value = saved.asOf;
  if (saved.judges !== undefined) $("f-judges").checked = Boolean(saved.judges);
  if (saved.rounds !== undefined) $("f-rounds").value = saved.rounds;
  if (saved.riskRounds !== undefined) $("f-risk-rounds").value = saved.riskRounds;
  if (saved.tools !== undefined) $("f-tools").checked = Boolean(saved.tools);
  if (saved.memory !== undefined) $("f-memory").checked = Boolean(saved.memory);
  if (saved.portfolio !== undefined) $("f-portfolio").value = saved.portfolio;
  if (saved.channels)
    for (const [name, on] of Object.entries(saved.channels)) {
      const box = $("fc-" + name);
      if (box) box.checked = on === true && name !== "macro";   /* macro 的勾等 news 一起恢复 */
    }
  const news = saved.channels ? saved.channels.news === true : false;
  const macro = saved.channels ? saved.channels.macro === true : false;
  if (news && macro) $("fc-macro").checked = true;
}

function applyScenario() {
  /* 90-boot 在 meta 到手后会调一次，之后每次换场景也调：第一次把缺省值与上次的值放进去 */
  if (state.meta && !metaApplied) { metaApplied = true; applyMetaDefaults(); }
  if (!savedApplied) {
    savedApplied = true;
    applySaved(); updateMacroGate(); updateTickerHint(); schedulePlan();
  }
  const name = $("f-scenario").value;
  const scenario = state.meta && state.meta.scenarios.find((s) => s.name === name);
  const note = $("scenario-note");
  note.hidden = !scenario;
  for (const id of ["f-judges", "f-rounds", "f-risk-rounds", "f-tools", "f-memory"]) $(id).disabled = Boolean(scenario);
  setChannelsDisabled(Boolean(scenario));   /* 场景自带流程与通道（组合文件仍可给：--portfolio 优先于场景内联） */
  if (!scenario) return;
  $("f-ticker").value = scenario.ticker;
  $("f-date").value = scenario.as_of;
  note.textContent = "场景 " + scenario.name + "：" + scenario.about + " 评审与辩论轮数用场景自己的配置。";
  updateTickerHint();
}

/* ---------- 计划预览 ---------- */

function schedulePlan() {
  if (!planAvailable) return;
  clearTimeout(planTimer);
  planTimer = setTimeout(refreshPlan, 250);            /* 改动停顿一下再问，别每敲一个字问一次 */
}

async function refreshPlan() {
  if (!planAvailable || !state.meta) return;
  const seq = planSeq += 1;
  const { status, data } = await getJSON("/api/plan",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formBody(snapshot())) });
  if (seq !== planSeq || !state.meta) return;          /* 过期的回复：表单又改过了 */
  if (status === 404) { planAvailable = false; $("plan").hidden = true; return; }   /* 服务端没有这个接口：整个预览收起 */
  const box = $("plan");
  box.hidden = false;
  if (status === 422) {
    fill(box, el("h3", null, "计划预览"), el("p", { class: "error" },
      "这次请求会被拒绝：" + ((data && data.error) || "请求不合格式") + "。"));
    return;
  }
  if (status !== 200) return;
  renderPlan(planRows(data));
}

function renderPlan(rows) {
  const box = $("plan");
  if (!rows) { box.hidden = true; return; }
  const stageChip = (stage) => el("span", { class: "stage idle", title: stage.roles.join("、") || undefined },
    STAGE_NAME[stage.key] || stage.key);
  const chips = [];
  let i = 0;
  while (i < rows.stages.length) {                      /* 连续的并行段画进同一个框，像流水线那样 */
    if (!rows.stages[i].parallel) { chips.push(stageChip(rows.stages[i])); i += 1; continue; }
    const group = [rows.stages[i]];
    while (i + 1 < rows.stages.length && rows.stages[i + 1].parallel) { i += 1; group.push(rows.stages[i]); }
    chips.push(el("span", { class: "parallel", title: "这几位分析师同时运行" },
      el("span", { class: "par-tag" }, "并行"), group.map(stageChip)));
    i += 1;
  }
  const kids = [el("h3", null, "计划预览"),
    el("p", { class: "note" }, "这次请求会走的阶段与开销的估计；不调用模型，不产生运行。"),
    el("div", { class: "plan-line" },
      "标的 " + (rows.ticker || "—") + " · 分析日 " + (rows.asOf || "最近交易日")
      + " · " + (MODE_TEXT[rows.mode] || rows.mode || "")),
    el("div", { class: "plan-strip" }, chips),
    el("div", { class: "plan-line" },
      "调用：典型 " + (rows.calls.typical !== null ? count(rows.calls.typical) : "—") + " 次 · 上限 "
      + (rows.calls.ceiling !== null && rows.calls.ceiling !== undefined ? count(rows.calls.ceiling) : "不设上限")),
    el("div", { class: "plan-line" },
      "token 上限 " + (rows.runTokens ? count(rows.runTokens) : "不设上限")
      + " · 成本上限 " + (rows.runCost !== null && rows.runCost !== undefined ? num(rows.runCost, 6) : "不设上限"))];
  if (rows.channels.length) {
    kids.push(el("div", { class: "plan-channels" }, rows.channels.map((channel) =>
      el("span", { class: "plan-chan st-" + (channel.state || "off"),
                   title: channel.reason || undefined },
        channel.name + " " + (PLAN_CHANNEL_WORD[channel.state] || channel.state || "—")
        + (channel.state === "unavailable" && channel.reason ? "（" + channel.reason + "）" : "")))));
  }
  if (rows.portfolio !== null && rows.portfolio !== "") {
    kids.push(el("div", { class: "plan-line" },
      "组合：" + (PSTATE_TEXT[rows.portfolio] || rows.portfolio)));
  }
  fill(box, kids);
}

/* ---------- 提交与启动键 ---------- */

function fieldError(which, text) {
  const box = $("err-" + which);
  if (box) box.textContent = text || "";
}

function clearFieldErrors() { for (const which of ["ticker", "date", "channels", "portfolio"]) fieldError(which, ""); }

function errorField(message) {
  /* 服务器的一条拒绝理由放回它说的那个字段旁边；说不清就放表单级错误 */
  const text = String(message || "");
  if (text.includes("代码") || text.includes("标的")) return "ticker";
  if (text.includes("日期") || text.includes("数据")) return "date";
  if (text.includes("通道") || text.includes("宏观")) return "channels";
  if (text.includes("组合")) return "portfolio";
  return "";
}

async function submitForm(ev) {
  ev.preventDefault();
  const error = $("form-error");
  error.hidden = true;
  clearFieldErrors();
  const values = snapshot();
  remember();
  const body = formBody(values);
  startPending = true; updateLaunchState();
  let reply;
  try {
    reply = await getJSON("/api/runs",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (_) {
    reply = { status: 0, data: null };
  } finally {
    startPending = false;
  }
  if (reply.status === 202) { followLive(reply.data.run_id); return; }
  const message = (reply.data && reply.data.error) || ("请求不合格式（HTTP " + reply.status + "）");
  const which = errorField(message);
  if (which) fieldError(which, message);
  else {
    error.hidden = false;
    fill(error, "没有开始：" + message + "。",
      reply.data && reply.data.active_run ? el("button", { class: "linkish", type: "button",
        on: { click: () => { openRun(reply.data.active_run, false); window.scrollTo(0, 0); } } },
        "查看正在进行的运行") : null);
  }
  updateLaunchState();
}

function followLive(runId) {
  /* 看现场：事件到了就画（0.7 秒的逐条揭示只用于原始事件页签的回放） */
  openRun(runId, false);
  state.follow = true;
}

function updateLaunchState() {
  const button = $("btn-start");
  if (!button) return;
  const active = Boolean(state.runId) && !state.ended;   /* 有一个未收尾的运行（就是活动运行） */
  button.disabled = startPending || active;              /* 请求在途或运行进行中：不能开始下一次 */
  button.textContent = startPending ? "开始……" : "开始";
  const fold = $("launch-fold"), hint = $("fold-hint");
  if (fold && state.runId !== foldedFor) {               /* 打开一个运行时把发起区折成一行；回到列表再展开 */
    foldedFor = state.runId;
    fold.open = !state.runId;
  }
  if (hint) hint.textContent = active ? "（有一次运行正在进行）" : "";
}

/* ---------- 历史 ---------- */

async function loadHistory() {
  const { status, data } = await getJSON("/api/runs");
  const body = $("history-body");
  if (status !== 200) {                            // 读不到列表：就地给重试（质量清单「失败的请求有重试」）
    fill(body, el("span", { class: "muted" }, "读不到运行列表。"),
      el("button", { class: "linkish", type: "button", on: { click: loadHistory } }, "重试"));
    return;
  }
  if (!data.runs.length) { body.textContent = "还没有运行。"; return; }
  const when = (id) => id.slice(0, 4) + "-" + id.slice(4, 6) + "-" + id.slice(6, 8) + " "
    + id.slice(9, 11) + ":" + id.slice(11, 13) + ":" + id.slice(13, 15);
  fill(body, table(["开始时间", "代码", "分析日", "模型", "结果", "原因", ""], data.runs.map((run) => [
    when(run.run_id), run.ticker, run.as_of, MODE_TEXT[run.mode] || run.mode,
    el("span", { class: run.status === "delivered" ? "pass" : run.status === "review" ? "fail" : "na" },
       STATUS_TEXT[run.status] || run.status),
    run.reason === "ok" ? "" : reasonText(run.reason),
    el("span", { class: "rowctl" },
      run.resumable ? el("button", { class: "linkish", type: "button",
        on: { click: () => resumeRun(run.run_id) } }, "续跑") : null,
      el("button", { class: "linkish", type: "button",
        on: { click: () => { openRun(run.run_id, false); window.scrollTo(0, 0); } } }, "查看"))])));
}

/* 实时模式才重排表单：静态 replay.html 的发起区整个隐藏，不进来 */
if (typeof document !== "undefined" && document.getElementById("launch")
    && document.getElementById("embedded")
    && !JSON.parse(document.getElementById("embedded").textContent)) buildLaunch();
