/* 25-activity.js —— 运行状态与实时活动（WP50 接手，BLUEPRINT 20.10「实时活动」）。
   流水线每段五种状态：等待 / 进行中 / 完成 / 被打回（带次数）/ 跳过（通道不可用）；
   正在工作的角色各带一个每秒走动的计时器；已用时与「模型调用中」提示；
   页头的取消、续跑与导出（它们都要本地服务，静态 replay.html 一个也不出现）。 */

function stageOf(e) {
  if (e.type === "gate.checked") return e.data.gate;
  if (!["model.called", "model.failed", "budget.exceeded", "artifact.written"].includes(e.type)) return null;
  const actor = e.actor || "";
  if (actor.startsWith("judge.")) return "judges";
  if (e.type === "artifact.written") {
    for (const [prefix, stage] of KEY_STAGE) if (e.data.key.startsWith(prefix)) return stage;
  }
  return ACTOR_STAGE[actor] || null;
}

const CHANNEL_KEY_STAGE = Object.fromEntries(Object.entries(CHANNEL_STAGES).map(([stage, key]) => [key, stage]));

function pipeline(events, interrupted) {
  /* 每段的状态：waiting（未在结果里）/ running / done / halted（运行在此停下）/ skipped（通道不可用）。
     被打回不算单独的状态：目标段与它之后的各段回到未开始，目标段记一次返工；
     完成后又被打回重跑过的段，展示时算「被打回 ↺n」（renderPipeline 里换算）。
     current 是正在进行的那一段：闸门通过后轮到下一段，被打回后轮到被点名的那一段。
     interrupted 为真（回放或已中断的运行）时，current 不画「进行中」而画「未完成」：它已经不在跑了。 */
  const status = {}, redo = {}, channels = {};
  let last = null, current = null, finished = null;
  for (const e of events) {
    if (e.type === "route") {
      for (const stage of STAGE_ORDER.slice(STAGE_ORDER.indexOf(e.data.to))) delete status[stage];
      redo[e.data.to] = (redo[e.data.to] || 0) + 1;
      last = null; current = e.data.to;
    } else if (e.type === "run.finished") {
      finished = e;
    } else {
      if (e.type === "artifact.written" && CHANNEL_KEY_STAGE[e.data.key]) {
        const value = e.data.value || {};                       // 通道自己的状态键：不可用 ⇒ 这段跳过
        channels[CHANNEL_KEY_STAGE[e.data.key]] = { state: value.state || "", reason: value.reason || "" };
      }
      const stage = stageOf(e);
      if (!stage) continue;
      if (stage !== "report") last = stage;
      const stopped = e.type === "model.failed" || e.type === "budget.exceeded"
        || (e.type === "gate.checked" && e.data.findings > 0)
        || (e.type === "model.called" && e.data.output === null && stage !== "judges");
      status[stage] = stopped ? "halted" : "done";
      const passed = e.type === "gate.checked" && !stopped;
      current = stopped ? null : passed ? STAGE_ORDER[STAGE_ORDER.indexOf(stage) + 1] || null : stage;
    }
  }
  for (const [stage, info] of Object.entries(channels)) {
    if (info.state === "unavailable") status[stage] = "skipped";  // 证据阶段写明不可用：分析师这段没有跑
  }
  if (finished) {
    status.report = "done";
    if (finished.data.status !== "delivered" && last && status[last] === "done") status[last] = "halted";
  } else if (current) {
    status[current] = interrupted ? "halted" : "running";
  }
  return { status, redo, channels };
}

/* ---------- 计时（纯函数，Node 测试直接调） ---------- */

function tsMs(e) {
  const t = Date.parse((e && e.ts) || "");
  return Number.isNaN(t) ? null : t;
}

function elapsedText(ms) {
  if (ms === null || ms === undefined || ms < 0) return "—";
  const s = Math.floor(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  const two = (n) => String(n).padStart(2, "0");
  return h > 0 ? h + ":" + two(m) + ":" + two(s % 60) : m + ":" + two(s % 60);
}

function runElapsed(events, nowMs) {
  /* 总用时：run.started 的时刻到 run.finished 的时刻；没有结束事件时，实时看最后一条事件之后仍在走，
     回放与已中断的运行停在最后已知的时刻。 */
  let start = null, finishedAt = null, lastAt = null;
  for (const e of events) {
    const t = tsMs(e);
    if (t === null) continue;
    if (e.type === "run.started" && start === null) start = t;
    if (e.type === "run.finished") finishedAt = t;
    lastAt = t;
  }
  if (start === null) return null;
  const end = finishedAt !== null ? finishedAt : (nowMs !== null && nowMs !== undefined ? Math.max(nowMs, lastAt || 0) : (lastAt !== null ? lastAt : 0));
  return Math.max(0, end - start);
}

function activityRoles(spans, nowMs) {
  /* 轨迹里开着头没收尾的跨度 = 正在飞行中的调用：每人一行计时器（实时时每秒走动） */
  const roles = [];
  for (const span of spans || []) {
    if (!span || span.end !== null) continue;
    const started = Date.parse(span.start || "");
    if (Number.isNaN(started) || nowMs === null || nowMs === undefined) continue;
    const lane = LANES.find(([key]) => key === span.role);
    roles.push({ role: span.role, step: span.step || "", label: lane ? lane[1] : span.role || "",
                 startedMs: started, elapsedMs: Math.max(0, nowMs - started) });
  }
  roles.sort((a, b) => a.startedMs - b.startedMs);
  return roles;
}

/* ---------- 运行状态 ---------- */

function renderRunState(model) {
  const pill = $("run-state");
  cancelModel = model;                               /* 取消确认层用它写「截至目前的用量」 */
  if (!model.started && !state.runId) { pill.hidden = true; return; }
  pill.hidden = false;
  /* 流已经收尾、日志里却没有 run.finished：实时页上这是一次中断，不是「进行中」（模型层的 live 只说页面由服务提供） */
  const interrupted = state.live && state.ended && !model.finished;
  pill.className = "pill st-" + (interrupted ? "interrupted" : model.status);
  pill.textContent = interrupted ? (STATUS_TEXT.interrupted || "已中断") : (STATUS_TEXT[model.status] || model.status);
  updateRunControls(model);
}

/* ---------- 流水线 ---------- */

const STAGE_WORD = { waiting: "等待", running: "进行中", done: "完成", sentback: "被打回", skipped: "跳过", halted: "未完成" };

function stageState(info, key) {
  /* 完成但被打回重跑过的段，展示算「被打回」（带次数） */
  const state = info.status[key] || "waiting";
  return state === "done" && info.redo[key] ? "sentback" : state;
}

function renderPipeline(model) {
  const events = model.events;
  const watching = state.live && !state.ended;                  /* 正在看现场（流还开着） */
  const info = pipeline(events, !watching && !model.finished);  /* 回放/已中断：停下的段画「未完成」 */
  const frozen = watching ? Date.now() : frozenNow(model);      /* 现场用当前时刻，回放/中断停在最后已知时刻 */
  const working = activityRoles(model.spans, frozen);           /* 在飞的调用：读屏播报与计时行同一份 */
  announceActivity(info, working);
  const board = model.board;
  const started = model.started;
  const parallel = started && started.flow && started.flow.parallel !== false;
  /* WP56：照原型的 .stage 列解剖——rail 连接线 + 32px 圆节点（完成 ✓、未完成 ✕、跳过 —、
     被打回 ↺n、其余序号），名字与状态词在下方，不只靠颜色 */
  const node = (key) => {
    const entry = STAGES.find(([k]) => k === key);
    const st = stageState(info, key);
    const cls = st === "sentback" ? "revised" : st;
    let mark = String(STAGES.findIndex(([k]) => k === key) + 1).padStart(2, "0");
    if (st === "done") mark = icon("check");
    else if (st === "sentback") mark = "↺" + info.redo[key];
    else if (st === "halted") mark = icon("x");
    else if (st === "skipped") mark = icon("minus");
    let title = entry[2] === "model" ? "由模型完成" : "由代码完成";
    if (st === "sentback") title = "被打回 " + info.redo[key] + " 次后重跑完成（" + title + "）";
    if (st === "skipped") title = "跳过：通道不可用" + (info.channels[key] && info.channels[key].reason ? "（" + info.channels[key].reason + "）" : "");
    if (st === "halted") title = "运行在这里停下（" + title + "）";
    return el("span", { class: "pnode st-" + cls, title },
      el("span", { class: "pnode-rail" }, el("span", { class: "pnode-dot" }, mark)),
      el("b", { class: "pnode-name" }, entry[1]),
      el("span", { class: "pnode-line" },
        el("span", { class: "st" }, STAGE_WORD[st] || st),
        el("span", { class: "kind" }, entry[2] === "model" ? "模型" : "代码")));
  };
  const visible = STAGES.map(([key]) => key)
    .filter((key) => !CHANNEL_STAGES[key] || board[CHANNEL_STAGES[key]]);
  const grouped = parallel && ANALYST_KEYS.filter((key) => visible.includes(key)).length > 1;
  const kids = [];
  visible.forEach((key) => {
    const inGroup = grouped && ANALYST_KEYS.includes(key);
    const leadsGroup = inGroup && key === ANALYST_KEYS[0];
    if (leadsGroup) {                                   /* 并行模式：几位分析师同时跑，画成一个框里并列 */
      const group = el("span", { class: "parallel", title: "这几位分析师同时运行，产物按固定顺序落账" },
        el("span", { class: "par-tag" }, "并行"));
      for (const k of ANALYST_KEYS) if (visible.includes(k)) group.append(node(k));
      kids.push(group);
      return;
    }
    if (inGroup) return;                                /* 组里已经画过 */
    kids.push(node(key));
  });

  /* 正在工作的角色（轨迹给的在飞跨度）＋总用时＋「模型调用中」提示 */
  if (working.length) {
    kids.push(el("div", { class: "working", title: "轨迹里开着头没收尾的调用：这些角色此刻在等模型返回" },
      working.map((w) => el("span", { class: "work-role" },
        el("b", null, w.label), el("span", { class: "mono ms" }, elapsedText(w.elapsedMs))))));
  }
  const total = runElapsed(events, watching ? Date.now() : null);
  const meta = [];
  if (model.started && total !== null) meta.push((watching ? "已用时 " : "总用时 ") + elapsedText(total));
  if (watching || working.length) meta.push("模型调用中：结果返回后一次显示");
  if (meta.length) kids.push(el("div", { class: "activity-meta" },
    meta.map((text) => el("span", { class: "meta-bit" }, text))));
  fill($("pipeline"), kids);
}

/* 读屏：活动区的变化（进行到哪一段、谁在飞）变化时播报一次（20.10 质量清单「活动区是活区」）。
   计时器每秒走的数字不进摘要，免得读屏每秒念一遍；只有段的状态集合与在飞角色集合变了才说。 */
let announcedActivity = "";
function stageTitleOf(key) {
  const entry = STAGES.find(([k]) => k === key);
  return entry ? entry[1] : key;
}
function announceActivity(info, working) {
  const box = $("sr-activity");
  if (!box) return;
  const running = Object.keys(info.status).filter((key) => info.status[key] === "running");
  const halted = Object.keys(info.status).filter((key) => info.status[key] === "halted");
  const done = Object.keys(info.status)
    .filter((key) => info.status[key] === "done" || info.status[key] === "sentback").length;
  const parts = [];
  if (running.length) parts.push("进行中：" + running.map(stageTitleOf).join("、"));
  if (halted.length) parts.push("未完成：" + halted.map(stageTitleOf).join("、"));
  if (working.length) parts.push("模型调用中：" + working.map((w) => w.label).join("、"));
  if (done) parts.push("已完成 " + done + " 段");
  const text = parts.join("；");
  if (text === announcedActivity) return;
  announcedActivity = text;
  box.textContent = text;
}

function frozenNow(model) {
  /* 非实时（回放、静态页）：最后已知的时刻，作为计时的终点 */
  let latest = null;
  for (const e of model.events) {
    const t = tsMs(e);
    if (t !== null && (latest === null || t > latest)) latest = t;
  }
  for (const line of state.trace || []) {
    const t = Date.parse((line && line.ts) || "");
    if (!Number.isNaN(t) && (latest === null || t > latest)) latest = t;
  }
  return latest;
}

/* ---------- 事件流上的轨迹（20.8 第 4 条：trace 行随流送上，连接时整份重发） ---------- */

function hookTrace() {
  const source = state.source;
  if (!source || source.__traceHooked) return;          /* 每个连接挂一次；重连是新对象，会再挂 */
  source.__traceHooked = true;
  state.trace = [];                                     /* 连接时重置自己的副本，整份重收 */
  source.addEventListener("trace", (message) => {
    try { state.trace.push(JSON.parse(message.data)); } catch (_) { return; }
    requestRender();
  });
}

/* ---------- 页头的取消、续跑与导出 ---------- */

let cancelPending = false, resumePending = false, noteRun = "", cancelModel = null;
const exportProbe = new Map();                          /* runId → { report, replay, asked } */

function runNote(text) {
  const note = $("runctl-note");
  if (note) note.textContent = text;
}

function buildRunControls() {
  const host = document.querySelector(".head-side");
  const actions = $("ws-actions");                 /* WP55：导出键归位到大符号标题区 */
  if (!host || !$("run-state")) return;
  host.append(el("span", { class: "runctl" },
    el("button", { class: "ctl warn", type: "button", id: "btn-cancel", hidden: true,
                   on: { click: cancelRun } }, icon("square"), "取消"),
    el("button", { class: "ctl", type: "button", id: "btn-resume", hidden: true,
                   on: { click: () => resumeRun(state.runId) } }, icon("play"), "续跑"),
    el("span", { class: "runctl-note", id: "runctl-note" })));
  if (actions) actions.append(el("span", { class: "runctl" },
    el("a", { class: "ctl", id: "export-report", hidden: true }, icon("download"), "导出报告"),
    el("a", { class: "ctl", id: "export-replay", hidden: true }, icon("history"), "导出回放")));
  setInterval(() => {                                   /* 计时器每秒走一格 */
    if (state.live && state.runId && !state.ended) requestRender();
  }, 1000);
}

function updateRunControls(model) {
  if (!state.live) return;
  hookTrace();
  const cancel = $("btn-cancel"), resume = $("btn-resume");
  if (!cancel || !resume) return;
  const inFlight = Boolean(state.runId) && !state.ended && !model.finished;
  const interrupted = Boolean(state.runId) && state.ended && !model.finished;
  cancel.hidden = !inFlight;
  cancel.disabled = cancelPending;
  resume.hidden = !interrupted;
  resume.disabled = resumePending;
  if (state.runId !== noteRun) { noteRun = state.runId; runNote(""); }
  const links = exportLinks(state.runId);
  const report = $("export-report"), replay = $("export-replay");
  report.hidden = !links.report;
  replay.hidden = !links.replay;
  if (links.report) report.setAttribute("href", "/api/runs/" + encodeURIComponent(state.runId) + "/report");
  if (links.replay) replay.setAttribute("href", "/api/runs/" + encodeURIComponent(state.runId) + "/replay");
  if (typeof updateLaunchState === "function") updateLaunchState();
}

function exportLinks(runId) {
  if (!runId) return { report: false, replay: false };
  const entry = exportProbe.get(runId) || { report: false, replay: false, asked: false };
  if (!entry.asked && state.ended) {                    /* 文件在运行收尾时才写：收尾后各探一次 */
    entry.asked = true;
    for (const [field, path] of [["report", "/api/runs/" + encodeURIComponent(runId) + "/report"],
                                 ["replay", "/api/runs/" + encodeURIComponent(runId) + "/replay"]]) {
      fetch(path, { method: "HEAD" }).then((response) => {
        const current = exportProbe.get(runId);
        if (current) { current[field] = response.status === 200; requestRender(); }
      }).catch(() => {});
    }
  }
  exportProbe.set(runId, entry);
  return entry;
}

function cancelRun() {
  /* 取消先过确认层（WP56 照原型的 modal：写清在飞调用不记用量、可续跑）；确认才发请求 */
  if (cancelPending || !state.runId) return;
  const dialog = $("cancel-dialog");
  if (!dialog || typeof dialog.showModal !== "function") { void requestCancel(); return; }
  const cost = $("cancel-cost");
  if (cost && cancelModel) {
    const total = runElapsed(cancelModel.events, state.live && !state.ended ? Date.now() : null);
    const calls = cancelModel.events.filter((e) => e.type === "model.called").length;
    cost.textContent = (total !== null ? elapsedText(total) : "—") + " · 模型调用 " + calls + " 次";
  }
  dialog.showModal();
}

async function requestCancel() {
  if (cancelPending || !state.runId) return;
  cancelPending = true; runNote(""); requestRender();
  const runId = state.runId;
  let reply;
  try { reply = await getJSON("/api/runs/" + encodeURIComponent(runId) + "/cancel", { method: "POST" }); }
  catch (_) { reply = { status: 0, data: null }; }
  finally { cancelPending = false; }
  if (state.runId === runId) {
    if (reply.status === 202) runNote("已请求取消；等它收尾后可以续跑。");
    else if (reply.status === 409) runNote((reply.data && reply.data.error) || "现在不能取消这个运行。");
    else if (reply.status === 404) runNote("这个服务还没有取消接口（等 WP45 的服务端）。");
    else runNote("取消没有成功（HTTP " + reply.status + "）。");
  }
  requestRender(); loadHistory();
}

async function resumeRun(runId) {
  if (resumePending || !runId) return;
  resumePending = true; runNote(""); requestRender();
  let reply;
  try { reply = await getJSON("/api/runs/" + encodeURIComponent(runId) + "/resume", { method: "POST" }); }
  catch (_) { reply = { status: 0, data: null }; }
  finally { resumePending = false; }
  if (reply.status === 202) { followLive(runId); return; }
  if (reply.status === 409) runNote((reply.data && reply.data.error) || "现在不能续跑这个运行。");
  else if (reply.status === 404) runNote("这个服务还没有续跑接口（等 WP45 的服务端）。");
  else runNote("续跑没有成功（HTTP " + reply.status + "）。");
  requestRender(); loadHistory();
}

/* 取消确认层的三个键（WP56）：确认才发请求，关闭与「继续观察」只收层 */
if (typeof document !== "undefined" && $("cancel-dialog")) {
  const dialog = $("cancel-dialog");
  $("cancel-close").addEventListener("click", () => dialog.close());
  $("cancel-keep").addEventListener("click", () => dialog.close());
  $("cancel-confirm").addEventListener("click", () => { dialog.close(); void requestCancel(); });
}

/* 实时模式才有页头控制：静态 replay.html（事件嵌在页面里）没有服务，一个都不画 */
if (typeof document !== "undefined" && document.getElementById("embedded")
    && !JSON.parse(document.getElementById("embedded").textContent)) buildRunControls();
