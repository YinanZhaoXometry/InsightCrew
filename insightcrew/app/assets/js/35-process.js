/* 35-process.js —— 过程页签（WP47，BLUEPRINT 20.10「Process」）。
   按阶段把整个运行讲一遍：分析师（并行并列）、多空辩论、研究主管、交易员与审计打回的
   修订对照、风险三方意见、评审意见。每张卡片都能打开事件抽屉，看这一次调用的提示词
   与原始回复；被取代的旧版本默认折叠。页面只展示事件与轨迹里现成的值，不计算事实。
   本文件加载时不碰 document：纯推导（processTurns / revisionsOf / debateRounds /
   riskRounds / judgeRounds / riskRevision）可被 Node 测试直接加载。 */

/* ---------- 私有标签（共享标签表没有的；缺失一律回落原文，不出现空白） ---------- */

const CONF_TEXT = { low: "低", medium: "中", high: "高" };          // 契约里的“把握”
const JUDGE_TARGET_TEXT = { thesis: "论点", levels: "价位与仓位", none: "" };
const PROPOSAL_ROWS = [["action", "操作"], ["entry", "入场价"], ["stop", "止损价"],
                       ["target", "目标价"], ["size_pct", "仓位（%）"]];
const THESIS_ROWS = [["rating", "评级"], ["horizon_days", "持有（交易日）"], ["summary", "论断"]];
const RISK_STANCES = ["aggressive", "conservative", "neutral"];     // 一轮之内三位风控的发言顺序
const laneName = (actor) => {
  const lane = LANES.find(([key]) => key === actor);
  return lane ? lane[1] : (actor || "系统");
};
const judgeName = (lens) => {
  const entry = JUDGES.find(([key]) => key === lens);
  return entry ? entry[1] : (lens || "");
};

/* ---------- 纯推导：角色的每次出场、轮次、修订 ---------- */

function processTurns(model) {
  /* 每个角色（模型）的每次出场：一次模型调用或一次失败，配上它写下的产物版本；
     被取代的旧版本标记 superseded。产物归属：artifact.caused_by 里同角色的那次调用，
     缺因果链时（如 flow 代写的反对票）退回它之前最近的同角色出场。 */
  const callBySeq = new Map(model.calls.map((c) => [c.seq, c]));
  const turns = [];
  const byActor = new Map();
  for (const e of model.events) {
    if (e.type !== "model.called" && e.type !== "model.failed") continue;
    const actor = e.actor || "";
    const turn = { seq: e.seq, actor, step: e.step || "", failed: e.type === "model.failed",
                   call: e.type === "model.called" ? callBySeq.get(e.seq) || null : null,
                   key: "", version: 0, superseded: false };
    turns.push(turn);
    if (!byActor.has(actor)) byActor.set(actor, []);
    byActor.get(actor).push(turn);
  }
  for (const e of model.events) {
    if (e.type !== "artifact.written") continue;
    const list = byActor.get(e.actor || "") || [];
    let owner = null;
    for (const seq of e.caused_by || []) {
      const cand = list.find((t) => t.seq === seq);
      if (cand) owner = cand;
    }
    if (!owner) for (let i = list.length - 1; i >= 0; i -= 1)
      if (list[i].seq < e.seq) { owner = list[i]; break; }
    if (owner) { owner.key = e.data.key; owner.version = e.data.version; }
  }
  for (const turn of turns) {
    const versions = model.versions[turn.key];
    if (versions && versions.length)
      turn.superseded = turn.version < versions[versions.length - 1].version;
  }
  return turns;
}

function debateRounds(turns) {
  /* 多空辩论的轮次：第 n 轮 = 多方的第 n 次出场 + 空方的第 n 次出场 */
  const bulls = turns.filter((t) => t.actor === "bull");
  const bears = turns.filter((t) => t.actor === "bear");
  const rounds = [];
  for (let i = 0; i < Math.max(bulls.length, bears.length); i += 1)
    rounds.push({ round: i + 1, bull: bulls[i] || null, bear: bears[i] || null });
  return rounds;
}

function riskRounds(turns) {
  /* 风险辩论的轮次：第 n 轮 = 三位风控各自的第 n 次出场（aggressive → conservative → neutral） */
  const countOf = (stance) => turns.filter((t) => t.actor === "risk." + stance).length;
  const total = Math.max(0, ...RISK_STANCES.map(countOf));
  const rounds = [];
  for (let i = 0; i < total; i += 1)
    rounds.push({ round: i + 1,
                  views: RISK_STANCES.map((stance) => turns.filter((t) => t.actor === "risk." + stance)[i] || null)
                                     .filter(Boolean) });
  return rounds;
}

function judgeRounds(model, turns) {
  /* 评审的轮次：连续的评审询问以 judges/status 的写入收尾，汇总取那一版的值；
     没有汇总的一轮是进行中或被打断的一轮。 */
  const rounds = [];
  let current = [];
  for (const e of model.events) {
    if (e.type === "model.called" && (e.actor || "").startsWith("judge."))
      current.push(turns.find((t) => t.seq === e.seq) || null);
    else if (e.type === "artifact.written" && e.data.key === "judges/status" && current.length) {
      rounds.push({ turns: current.filter(Boolean), status: e.data.value, statusSeq: e.seq });
      current = [];
    }
  }
  if (current.length) rounds.push({ turns: current.filter(Boolean), status: null, statusSeq: 0 });
  return rounds;
}

function revisionsOf(model, key, rowSpec, judgeTarget) {
  /* 一个键的修订史：每版一列、每字段一行、改动的格子标出；
     每一版被谁打回——审计的检查项（或规则）只在路由点名这个键的主人时算它的账，
     评审的反对票只在路由把它打回时算，其余的取代是随上游重做或主动修订。 */
  const ownerRole = key === "trader/proposal" ? "trader"
    : key === "manager/thesis" ? "manager" : key.split("/")[0];
  const versions = model.versions[key] || [];
  if (versions.length < 2) return null;
  const columns = versions.map((v, i) => ({ version: v.version, seq: v.seq, value: v.value || {}, index: i }));
  const rows = rowSpec.map(([field, label]) => ({
    field, label,
    cells: columns.map((c) => c.value[field] === undefined || c.value[field] === null ? null : c.value[field]),
    changed: columns.map((c, i) => i > 0
      && JSON.stringify(columns[i - 1].value[field] === undefined ? null : columns[i - 1].value[field])
         !== JSON.stringify(c.value[field] === undefined ? null : c.value[field])),
  }));
  const back = columns.map((column, i) => {
    const next = columns[i + 1] || null;
    const upper = next ? next.seq : Infinity;
    const routesIn = model.routes.filter((r) => r.seq > column.seq && r.seq < upper)
                                 .sort((a, b) => a.seq - b.seq);
    const gate = model.gates.filter((g) => g.seq > column.seq && g.seq < upper && g.data.gate === "audit"
                                    && g.data.findings > 0)
                            .sort((a, b) => a.seq - b.seq)[0] || null;
    const judgeRoute = routesIn.find((r) => String(r.data.why || "").startsWith("评审")
                                     && r.data.to === judgeTarget) || null;
    const firstRoute = routesIn[0] || null;
    const byAudit = Boolean(gate)
      && (!firstRoute || firstRoute.data.to === ownerRole || !next);   // 没有下一版：审计否了它，运行到此为止
    const byJudges = Boolean(judgeRoute);
    if (!(byAudit || byJudges)) {
      if (firstRoute && next)                             // 被打回的是上游，这一版只是随流程重做
        return { column, rewritten: true, source: "downstream", gate: null, route: firstRoute, dissents: [] };
      return null;                                        // 正常被取代（如风险辩论后的主动修订）
    }
    const dissents = [];
    if (byJudges)
      for (const e of model.events)
        if (e.type === "artifact.written" && (e.actor || "").startsWith("judge.")
            && e.data.key.endsWith("/verdict") && e.seq > column.seq && e.seq < upper
            && e.data.value && e.data.value.accept === false)
          dissents.push({ lens: (e.actor || "").split(".")[1] || "", verdict: e.data.value });
    return { column, rewritten: Boolean(next), source: byAudit ? "audit" : "judges",
             gate, route: byAudit ? firstRoute : judgeRoute, dissents };
  }).filter(Boolean);
  return { key, columns, rows, back };
}

function riskRevision(model, turns) {
  /* 风险辩论后交易员的修订：辩论开始前的现行版 → 辩论结束后写下的第一版 */
  const versions = model.versions["trader/proposal"] || [];
  const riskSeqs = turns.filter((t) => (t.actor || "").startsWith("risk.")).map((t) => t.seq);
  if (!riskSeqs.length || versions.length < 2) return null;
  const first = Math.min(...riskSeqs), last = Math.max(...riskSeqs);
  const before = versions.filter((v) => v.seq < first).pop() || null;
  const after = versions.filter((v) => v.seq > last)[0] || null;
  if (!before || !after) return null;
  const changes = [];
  for (const [field, label] of PROPOSAL_ROWS) {
    const a = (before.value || {})[field], b = (after.value || {})[field];
    if (JSON.stringify(a) !== JSON.stringify(b))
      changes.push(label.replace(/（.*）/, "") + " "
        + fieldText(field, a, "") + " → " + fieldText(field, b, ""));
  }
  return { before, after, changes };
}

/* ---------- 卡片 ---------- */

function fieldText(field, value, currency) {
  /* 展示格式：价位带上货币、仓位带 %；null 是“未给出”，不是 0 */
  if (value === null || value === undefined) return "—";
  if (field === "entry" || field === "stop" || field === "target")
    return num(value) + (currency ? " " + currency : "");
  if (field === "size_pct") return num(value) + "%";
  return String(value);
}

function chipsOf(ids) {
  if (!ids || !ids.length) return null;
  return el("span", { class: "chips" }, ids.map((id) => cite(id)));
}

function turnMeta(turn) {
  /* 卡片头：角色标签 · 步号 · 模型 · 耗时（轨迹里有才显示）· token */
  const call = turn.call;
  const usage = (call && call.usage) || {};
  return [
    el("b", { class: "who" }, laneName(turn.actor)),
    el("span", { class: "mono step" }, turn.step || "#" + turn.seq),
    call ? el("span", { class: "meta-bit" }, call.model || "—") : null,
    call ? el("span", { class: "meta-bit" }, call.ms !== null && call.ms !== undefined
      ? count(call.ms) + " ms" : "耗时无记录") : null,
    call ? el("span", { class: "meta-bit" },
      count(usage.input_tokens) + "+" + count(usage.output_tokens) + " token") : null,
    call && call.tool_calls ? el("span", { class: "meta-bit" }, "工具 ×" + call.tool_calls) : null,
    call && call.retried ? el("span", { class: "meta-bit" }, "含纠正重试") : null,
    turn.superseded ? el("span", { class: "meta-bit old-tag" }, "已被新版取代") : null,
  ];
}

function processCard(turn, body) {
  /* 一张卡片 = 一次出场。被取代的旧版本默认折叠（details 不带 open）；每张都有
     「查看提示词与原始回复」，点开就是原始事件页签里今天那个事件详情抽屉。 */
  const kids = [el("summary", null,
    el("span", { class: "phead" }, el("span", { class: "role-avatar" }, laneName(turn.actor).slice(0, 1)),
      turnMeta(turn)),
    el("button", { class: "ctl raw small", type: "button", title: "打开事件抽屉：这次调用的提示词与原始回复",
                   on: { click: (ev) => { ev.preventDefault(); ev.stopPropagation(); openDetail(turn.seq); } } },
      "查看提示词与原始回复"))];
  if (turn.failed) {
    const data = turn.event && turn.event.data ? turn.event.data : {};
    kids.push(el("div", { class: "pbody" },
      el("p", { class: "fail" }, "模型调用失败，这次运行在这里被打断。"),
      data.error ? el("p", { class: "note" }, String(data.error)) : null));
  } else if (!turn.call || turn.call.output === null || turn.call.output === undefined) {
    kids.push(el("div", { class: "pbody" },
      el("p", { class: "fail" }, "纠正一次后仍不合契约，没有产物。")));
  } else {
    kids.push(el("div", { class: "pbody" }, body));
  }
  return el("details", { class: "pcard" + (turn.superseded ? " old" : "") + " actor-" + turn.actor,
                         open: turn.superseded ? null : "" }, kids);
}

function claimsList(claims) {
  if (!claims || !claims.length) return el("p", { class: "note" }, "（没有引用事实的论断。）");
  return el("ul", { class: "plist" }, claims.map((claim) => el("li", null,
    cite(claim.fact), " ", claim.reading || "")));
}

function signalsList(signals, idField) {
  if (!signals || !signals.length) return el("p", { class: "note" }, "（没有信号。）");
  return el("ul", { class: "plist" }, signals.map((signal) => el("li", null,
    cite(signal[idField]), " ",
    el("span", { class: signal.direction === "positive" ? "pass" : signal.direction === "negative" ? "fail" : "na" },
      DIRECTION_TEXT[signal.direction] || signal.direction),
    "——", signal.reading || "")));
}

function analystBody(turn) {
  /* 按契约渲染各分析师的产物：先叙事，再论断或信号（带引用徽章），把握与基调随契约出现 */
  const out = turn.call.output;
  const stage = turn.key.split("/")[0];
  const kids = [];
  if (out.narrative) kids.push(el("p", { class: "pnar" }, out.narrative));
  if (out.claims && out.claims.length) kids.push(claimsList(out.claims));
  if (out.signals && out.signals.length)
    kids.push(signalsList(out.signals, stage === "sentiment" ? "post" : "item"));
  const bits = [];
  if (out.stance !== undefined) bits.push("基调：" + (STANCE_TEXT[out.stance] || out.stance));
  if (out.band !== undefined) bits.push("情绪带：" + (BAND_TEXT[out.band] || out.band));
  if (out.score !== undefined) bits.push("情绪分 " + num(out.score) + "（0 最看空，10 最看多）");
  if (out.confidence !== undefined)
    bits.push("把握：" + (CONF_TEXT[out.confidence] || out.confidence));
  if (bits.length) kids.push(el("p", { class: "note" }, bits.join("；") + "。"));
  return kids;
}

function argumentBody(turn) {
  const out = turn.call.output;
  const kids = [];
  if (out.points && out.points.length)
    kids.push(el("ul", { class: "plist" }, out.points.map((point) => el("li", null,
      point.text || "", " ", chipsOf(point.cites)))));
  else kids.push(el("p", { class: "note" }, "（没有论点。）"));
  if (out.rebuttal) kids.push(el("p", { class: "rebuttal" }, el("b", null, "回应对方："), out.rebuttal));
  return kids;
}

function thesisBody(turn) {
  const out = turn.call.output;
  const kids = [el("p", { class: "pnar" },
    el("b", null, "评级 " + (out.rating || "—")), "，预期持有 " + (out.horizon_days === undefined ? "—" : out.horizon_days) + " 个交易日。"),
    out.summary ? el("p", { class: "pnar" }, out.summary) : null];
  if (out.points && out.points.length)
    kids.push(el("ul", { class: "plist" }, out.points.map((point) => el("li", null,
      point.text || "", " ", chipsOf(point.cites)))));
  if (out.invalidation && out.invalidation.length)
    kids.push(el("div", { class: "inval" }, el("b", null, "失效条件"),
      el("ul", null, out.invalidation.map((item) => el("li", null, item)))));
  return kids;
}

function proposalBody(turn, currency) {
  const out = turn.call.output;
  const kids = [el("p", { class: "pnar" },
    el("b", null, "操作 " + (out.action || "—")),
    "；入场 " + fieldText("entry", out.entry, currency),
    "，止损 " + fieldText("stop", out.stop, currency),
    "，目标 " + fieldText("target", out.target, currency),
    "，仓位 " + fieldText("size_pct", out.size_pct, ""),
    "，持有 " + (out.horizon_days === undefined ? "—" : out.horizon_days) + " 个交易日。")];
  if (out.rationale) kids.push(el("p", { class: "note" }, "方案说明：" + out.rationale));
  if (out.cites && out.cites.length) kids.push(el("p", { class: "note" }, "依据："), chipsOf(out.cites));
  return kids;
}

function riskBody(turn) {
  const out = turn.call.output;
  const kids = [];
  if (out.critique) kids.push(el("p", { class: "pnar" }, out.critique));
  kids.push(el("p", { class: "note" },
    "建议：仓位 " + (out.size_pct === null || out.size_pct === undefined ? "不改动" : fieldText("size_pct", out.size_pct, ""))
    + "，止损 " + (out.stop === null || out.stop === undefined ? "不改动" : fieldText("stop", out.stop, ""))
    + "，目标 " + (out.target === null || out.target === undefined ? "不改动" : fieldText("target", out.target, "")) + "。"));
  if (out.cites && out.cites.length) kids.push(chipsOf(out.cites));
  if (out.rebuttal) kids.push(el("p", { class: "rebuttal" }, el("b", null, "回应另两位："), out.rebuttal));
  return kids;
}

function verdictBody(turn) {
  const out = turn.call.output;
  const target = JUDGE_TARGET_TEXT[out.target] === undefined ? (out.target || "") : JUDGE_TARGET_TEXT[out.target];
  return [el("p", { class: "pnar" },
    el("span", { class: out.accept ? "pass" : "fail" }, out.accept ? "接受" : "反对"),
    target ? "（问题出在" + target + "）" : "",
    out.accept ? "" : "。评审只能否决，不能证明方案正确。"),
    out.critique ? el("p", { class: "note" }, out.critique) : null,
    chipsOf(out.cites)];
}

/* ---------- 各节 ---------- */

function sectionKids(model, turns, actors, hint) {
  /* 一节里属于这些角色的出场，按首次出现顺序；附一句在飞行中的提示（轨迹里有开始没收尾的跨度） */
  const picked = turns.filter((t) => actors.includes(t.actor));
  if (!picked.length) return null;
  const order = [];
  for (const turn of picked) if (!order.includes(turn.actor)) order.push(turn.actor);
  const working = model.spans.filter((s) => s.end === null && actors.includes(s.role))
    .map((s) => laneName(s.role));
  const kids = [];
  for (const actor of order)
    kids.push(el("div", { class: "pcol" },
      picked.filter((t) => t.actor === actor).map((t) => processCard(t, bodyOf(t, model)))));
  if (working.length) kids.push(el("p", { class: "note" }, working.join("、") + " 正在作答，结果返回后显示。"));
  if (hint) kids.push(el("p", { class: "note" }, hint));
  return kids;
}

function bodyOf(turn, model) {
  const currency = (model.board["ledger/instrument"] || {}).currency || "";
  if (turn.key.startsWith("technical/") || turn.key === "fundamentals/analysis"
      || turn.key === "disclosures/analysis" || turn.key === "news/analysis"
      || turn.key === "sentiment/analysis") return analystBody(turn);
  if (turn.key === "bull/argument" || turn.key === "bear/argument") return argumentBody(turn);
  if (turn.key === "manager/thesis") return thesisBody(turn);
  if (turn.key === "trader/proposal") return proposalBody(turn, currency);
  if (turn.key.startsWith("risk/")) return riskBody(turn);
  if (turn.key.endsWith("/verdict")) return verdictBody(turn);
  return [el("p", { class: "note" }, "（这一产物没有专门的展示，点右上看原始回复。）")];
}

function revisionsBlock(model, key, rowSpec, judgeTarget, title, currency) {
  /* 修订对照：一版一列、一字段一行、改动的格子标出；每版下面写清被谁打回、为什么 */
  const rev = revisionsOf(model, key, rowSpec, judgeTarget);
  if (!rev) return null;
  const kids = [el("h4", { class: "psub" }, title)];
  const header = ["字段"].concat(rev.columns.map((c, i) => {
    const entry = rev.back.find((b) => b.column.index === c.index);
    return "第 " + c.version + " 版" + (entry ? "（被打回）" : i === rev.columns.length - 1 ? "（现行）" : "");
  }));
  const rows = rev.rows.map((row) => [row.label].concat(row.cells.map((value, i) =>
    el("span", { class: row.changed[i] ? "diff" : null }, fieldText(row.field, value, currency)))));
  kids.push(el("div", { class: "tscroll" }, table(header, rows)));
  for (const entry of rev.back) {
    const parts = ["第 " + entry.column.version + " 版"];
    if (entry.source === "audit") {
      const findings = (entry.gate && entry.gate.data.detail) || [];
      parts.push("被审计打回（" + findings.length + " 个问题）");
      parts.push(entry.route ? "重做：" + (STAGE_NAME[entry.route.data.to] || entry.route.data.to)
                             : "打回次数已用尽，运行没有再重做");
      kids.push(el("div", { class: "sentback" }, el("p", { class: "note" }, parts.join("，") + "。"),
        el("ul", { class: "findings" }, findings.map((f) => el("li", null,
          el("span", { class: "mono" }, "[" + (CHECK_LABEL[f.check] || RULE_LABEL[f.check] || f.check) + "] "
            + (f.subject || "")), el("br"), f.message || "")))));
    } else if (entry.source === "judges") {
      parts.push("被评审否决");
      parts.push(entry.route ? "重做：" + (STAGE_NAME[entry.route.data.to] || entry.route.data.to)
                             : "返工次数已用尽，运行以 REVIEW 结束");
      kids.push(el("div", { class: "sentback" }, el("p", { class: "note" }, parts.join("，") + "。"),
        el("ul", { class: "findings" }, entry.dissents.map((d) => el("li", null,
          el("span", { class: "mono" }, "[" + judgeName(d.lens) + "] 反对"
            + (d.verdict.target && d.verdict.target !== "none"
               ? "（" + (JUDGE_TARGET_TEXT[d.verdict.target] || d.verdict.target) + "）" : "")),
          el("br"), d.verdict.critique || "")))));
    } else {
      parts.push("随上游重做：" + (STAGE_NAME[entry.route.data.to] || entry.route.data.to) + " 被打回后，流程从这里重新跑");
      kids.push(el("div", { class: "sentback" }, el("p", { class: "note" }, parts.join("，") + "。")));
    }
  }
  return kids;
}

function renderProcess(model, box) {
  if (!model.events.length) {
    fill(box, el("h2", null, "过程"),
      el("div", { class: "muted" }, "还没有运行。从上方发起一次运行，或从「过去的运行」里打开一次。"));
    return;
  }
  const kids = [el("h2", null, "过程"),
    el("p", { class: "note" }, "按阶段讲述这次运行：每张卡片是一位角色的一次出场（模型、耗时与 token 都来自日志与轨迹），"
      + "点卡片右上的「查看提示词与原始回复」可看这次调用的全部往来；被取代的旧版本默认折叠，点开即展开。")];
  if (model.status === "running")
    kids.push(el("p", { class: "note" }, "运行进行中：以下各节随事件到齐逐步补全；正在作答的角色在流水线区有计时。"));
  if (model.status === "interrupted")
    kids.push(el("p", { class: "note" }, "这次运行没有正常结束（已中断）。以下是中断之前已经发生的过程；已花费但未返回的调用不会出现在任何文件里。"));
  if (model.status === "review")
    kids.push(el("p", { class: "note" }, "这次运行以 REVIEW 结束（" + reasonText((model.finished || {}).reason)
      + "）。被否决的版本不是结论；打回与重做的经过见下面各节的修订对照。"));

  const turns = processTurns(model);
  const currency = (model.board["ledger/instrument"] || {}).currency || "";
  const parallel = ((model.started || {}).flow || {}).parallel !== false;

  const analysts = sectionKids(model, turns, ANALYST_KEYS,
    parallel ? null : "串行模式：几位分析师按固定顺序依次运行。");
  if (analysts) kids.push(el("h3", null, "分析师"), el("div", { class: "par-grid" }, analysts));

  const debate = debateRounds(turns).filter((r) => r.bull || r.bear);
  if (debate.length) {
    kids.push(el("h3", null, "多空辩论"), el("p", { class: "note" },
      "多位研究员的观点没有经过审计，其中的数字不保证与账本一致。"));
    for (const round of debate)
      kids.push(el("div", { class: "de-round" },
        el("div", { class: "round-tag" }, "第 " + round.round + " 轮"),
        el("div", { class: "de-two" },
          round.bull ? processCard(round.bull, bodyOf(round.bull, model)) : el("p", { class: "note" }, "（多方未发言）"),
          round.bear ? processCard(round.bear, bodyOf(round.bear, model)) : el("p", { class: "note" }, "（空方未发言）"))));
  }

  const manager = sectionKids(model, turns, ["manager"]);
  if (manager) {
    kids.push(el("h3", null, "研究主管"));
    kids.push(el("div", { class: "solo" }, manager));
    const thesisRev = revisionsBlock(model, "manager/thesis", THESIS_ROWS, "manager",
      "论点修订对照", currency);
    if (thesisRev) kids.push(el("div", { class: "rev" }, thesisRev));
  }

  const trader = sectionKids(model, turns, ["trader"]);
  if (trader) {
    kids.push(el("h3", null, "交易员"));
    kids.push(el("div", { class: "solo" }, trader));
    const proposalRev = revisionsBlock(model, "trader/proposal", PROPOSAL_ROWS, "trader",
      "方案修订对照（action / entry / stop / target / size_pct）", currency);
    if (proposalRev) kids.push(el("div", { class: "rev" }, proposalRev));
  }

  const risk = riskRounds(turns);
  if (risk.length) {
    kids.push(el("h3", null, "风险三方意见（未经审计的意见）"),
      el("p", { class: "note" }, "三位风控在审计之前评论交易员的方案；辩论只能通过交易员的修订改变方案。"
        + "这里建议的仓位与价位不是审计对象，交付的方案始终是审计通过的那一版。"));
    for (const round of risk)
      kids.push(el("div", { class: "de-round" },
        el("div", { class: "round-tag" }, "第 " + round.round + " 轮"),
        el("div", { class: "risk-three" },
          round.views.map((t) => processCard(t, bodyOf(t, model))))));
    const revision = riskRevision(model, turns);
    kids.push(el("p", { class: "note" }, revision
      ? "辩论后交易员修订了方案（" + (revision.changes.length ? revision.changes.join("；") : "字段未变")
        + "）。完整对照见上面「交易员」一节。"
      : "辩论后交易员没有再修订方案。"));
  }

  const judges = judgeRounds(model, turns);
  if (judges.length) {
    kids.push(el("h3", null, "评审（三个视角）"), el("p", { class: "note" },
      "评审只能否决，不能证明方案正确；每轮先到法定人数即止，剩下的评审不再询问。"));
    for (let i = 0; i < judges.length; i += 1) {
      const round = judges[i];
      kids.push(el("div", { class: "de-round" },
        el("div", { class: "round-tag" }, "第 " + (i + 1) + " 轮"),
        el("div", { class: "par-grid" }, round.turns.map((t) => processCard(t, bodyOf(t, model))))));
      const after = judges[i + 1] ? judges[i + 1].statusSeq : Infinity;
      const route = model.routes.filter((r) => r.seq > round.statusSeq && r.seq < after)
                                .sort((a, b) => a.seq - b.seq)[0] || null;
      kids.push(el("p", { class: "note quorum" }, round.status
        ? "汇总：" + (String(round.status).startsWith("accepted")
            ? "评审通过（" + String(round.status).slice("accepted".length).trim() + " 票接受）"
            : round.status === "contested" ? "评审相持：多数反对" : String(round.status))
          + (route ? "；被打回重做（" + (STAGE_NAME[route.data.to] || route.data.to) + "），经过见修订对照。" : "。")
        : "（这一轮还没有汇总：运行中断或正在进行。）"));
    }
  }

  if (!turns.length && model.status !== "running") {
    const failed = model.events.find((e) => e.type === "gate.checked" && e.data.findings > 0);
    kids.push(el("div", { class: "muted" },
      "这次运行没有任何模型出场" + (failed ? "：在" + (STAGE_NAME[failed.data.gate] || failed.data.gate)
        + "处被拒绝（" + (((failed.data.detail || [])[0] || {}).message || "") + "）" : "") + "。"));
  }
  fill(box, kids);
}

registerView({ id: "process", title: "过程", render: renderProcess });
