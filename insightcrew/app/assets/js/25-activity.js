/* 25-activity.js —— 运行状态与流水线：每段的状态是本视图从事件私推出的（WP50 接手）。 */

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

function pipeline(events) {
  /* 每一段的状态。被打回时，目标段与它之后的各段回到"未开始"，目标段记一次返工。
     current 是正在进行的那一段：闸门通过后轮到下一段，被打回后轮到被点名的那一段。 */
  const status = {}, redo = {};
  let last = null, current = null, finished = null;
  for (const e of events) {
    if (e.type === "route") {
      for (const stage of STAGE_ORDER.slice(STAGE_ORDER.indexOf(e.data.to))) delete status[stage];
      redo[e.data.to] = (redo[e.data.to] || 0) + 1;
      last = null; current = e.data.to;
    } else if (e.type === "run.finished") {
      finished = e;
    } else {
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
  if (finished) {
    status.report = "done";
    if (finished.data.status !== "delivered" && last && status[last] === "done") status[last] = "halted";
  } else if (current) {
    status[current] = "running";
  }
  return { status, redo };
}

/* ---------- 运行状态 ---------- */

function renderRunState(model) {
  const pill = $("run-state");
  if (!model.started && !state.runId) { pill.hidden = true; return; }
  pill.hidden = false;
  const text = STATUS_TEXT[model.status] || model.status;
  pill.className = "pill st-" + model.status;
  pill.textContent = text;
}

/* ---------- 流水线 ---------- */

function renderPipeline(model) {
  const events = model.events;
  const { status, redo } = pipeline(events);
  const board = model.board;
  const started = model.started;
  const wall = board["run/wall_ms"] || null;            // 每分析师墙钟：黑板没有这个键就不展示
  const parallel = started && started.flow && started.flow.parallel !== false;
  const chip = (key) => {
    const entry = STAGES.find(([k]) => k === key);
    let cls = status[key] || "idle";
    if (cls === "done" && redo[key]) cls = "revised";
    return el("span", { class: "stage " + cls,
                        title: entry[2] === "model" ? "由模型完成" : "由代码完成" },
      entry[1], el("span", { class: "kind" }, entry[2] === "model" ? "模型" : "代码"),
      redo[key] ? "↺" + redo[key] : null,
      wall && wall[key] !== undefined ? el("span", { class: "ms" }, wall[key] + " ms") : null);
  };
  const visible = STAGES.map(([key]) => key)
    .filter((key) => !CHANNEL_STAGES[key] || board[CHANNEL_STAGES[key]]);
  const grouped = parallel && ANALYST_KEYS.filter((key) => visible.includes(key)).length > 1;
  const chips = [];
  visible.forEach((key, i) => {
    const inGroup = grouped && ANALYST_KEYS.includes(key);
    if (i) chips.push(el("span", { class: "arrow" }, "→"));
    if (inGroup && key === ANALYST_KEYS[0]) {           /* 并行模式：几位分析师同时跑，画成一个框里并列 */
      const group = el("span", { class: "parallel", title: "这几位分析师同时运行，产物按固定顺序落账" },
        el("span", { class: "par-tag" }, "并行"));
      for (const k of ANALYST_KEYS) if (visible.includes(k)) group.append(chip(k));
      chips.push(group);
      return;
    }
    if (inGroup) return;                                /* 组里已经画过 */
    chips.push(chip(key));
  });
  fill($("pipeline"), chips);
}
