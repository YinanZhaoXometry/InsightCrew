/* 60-events.js —— 原始事件页签：泳道时间线、回放控制与事件详情（抽屉），一切照旧（BLUEPRINT 20.9）。 */

const PACE = { slow: 1500, normal: 700, fast: 220 };

/* ---------- 时间线 ---------- */

function describe(e) {
  const d = e.data;
  switch (e.type) {
    case "run.started": return ["开始", MODE_TEXT[d.mode] || d.mode, ""];
    case "artifact.written": return [d.key, "v" + d.version, "artifact"];
    case "model.called":
      return [d.output === null ? "输出不合契约" : "模型作答",
              count(d.usage.input_tokens) + "+" + count(d.usage.output_tokens) + " token" + (d.usage.requests > 1 ? " · 重试" : ""),
              d.output === null ? "bad" : "model"];
    case "model.failed": return ["模型调用失败", d.model || "", "bad"];
    case "gate.checked":
      return d.findings ? ["发现 " + d.findings + " 个问题", ((d.detail || [])[0] || {}).check || "", "bad"] : ["通过", "", "good"];
    case "route": return ["⤺ 打回", "从" + (STAGE_NAME[d.to] || d.to) + "重跑", "route"];
    case "budget.exceeded": return ["预算超限", d.scope || "", "bad"];
    case "run.finished":
      return d.status === "delivered" ? ["交付", d.reason === "ok" ? "" : reasonText(d.reason), "good"]
                                      : ["REVIEW", reasonText(d.reason), "bad"];
    default: return [e.type, "", ""];
  }
}

function renderTimeline(model) {
  const events = model.events;
  const holder = $("lanes");
  const known = LANES.map(([key]) => key);
  const present = new Set(state.events.map((e) => e.actor || "app"));
  const lanes = LANES.filter(([key]) => present.has(key))
    .concat([...present].filter((key) => !known.includes(key)).map((key) => [key, key, "code"]));
  const rowOf = Object.fromEntries(lanes.map(([key], i) => [key, i + 2]));
  const newest = {};                              // 每个键最新的版本号：更早的版本画上删除线
  for (const e of events) if (e.type === "artifact.written") newest[e.data.key] = e.data.version;

  const turns = [];                               // 同一个角色连续写下的事件并成一栏：一栏就是它的一次出场
  for (const e of events) {
    const actor = e.actor || "app", last = turns[turns.length - 1];
    if (last && last.actor === actor) last.events.push(e); else turns.push({ actor, events: [e] });
  }
  const latest = state.playing && events.length ? events[events.length - 1].seq : 0;

  const grid = el("div", { class: "grid" });
  const place = (node, row, column) => { node.style.gridRow = String(row); node.style.gridColumn = String(column); return node; };
  lanes.forEach(([key, name, kind]) => {
    const band = el("div", { class: "lane-bg" });
    band.style.gridRow = String(rowOf[key]);
    band.style.gridColumn = "1 / span " + (turns.length + 1);
    grid.append(band, place(el("div", { class: "lane-name" },
      el("span", { class: "tag " + kind }, kind === "model" ? "模型" : "代码"), name), rowOf[key], 1));
  });
  turns.forEach((turn, i) => {
    const column = i + 2, first = turn.events[0].seq, last = turn.events[turn.events.length - 1].seq;
    grid.append(place(el("div", { class: "tick" }, first === last ? "#" + first : "#" + first + "–" + last), 1, column));
    const cell = el("div", { class: "cell" });
    for (const e of turn.events) {
      const [title, sub, kind] = describe(e);
      const old = e.type === "artifact.written" && newest[e.data.key] !== e.data.version;
      cell.append(el("button", { type: "button", "data-seq": e.seq, title: title + (sub ? " · " + sub : ""),
        class: "ev " + kind + (old ? " old" : "") + (e.seq === state.selected ? " selected" : "")
          + (e.seq === latest ? " latest" : ""),
        on: { click: () => openDetail(e.seq) } }, el("b", null, title), el("span", null, sub)));
      if (e.type === "route" && rowOf[STAGE_LANE[e.data.to]]) {   // 打回画成回到它点名的那一段
        grid.append(place(el("div", { class: "cell" }, el("button", { type: "button", class: "ev return",
          title: e.data.why || "", on: { click: () => openDetail(e.seq) } },
          el("b", null, "↩ 从这里重跑"), el("span", null, e.data.why || ""))), rowOf[STAGE_LANE[e.data.to]], column));
      }
    }
    grid.append(place(cell, rowOf[turn.actor], column));
  });
  const left = holder.scrollLeft, top = holder.scrollTop;
  fill(holder, events.length ? grid : el("div", { class: "muted", style: "padding:14px" }, "还没有事件。按“播放”逐条揭示。"));
  holder.scrollLeft = left; holder.scrollTop = top;
  const cards = holder.querySelectorAll(".cell .ev:not(.return)");
  if (state.follow && cards.length) {              // 跟随：把最新一张卡片移到框的中间。只滚这个框，不动整页
    const box = holder.getBoundingClientRect(), card = cards[cards.length - 1].getBoundingClientRect();
    holder.scrollLeft += card.left + card.width / 2 - (box.left + box.width / 2);
    holder.scrollTop += card.top + card.height / 2 - (box.top + box.height / 2);
  }
}

/* ---------- 事件详情 ---------- */

/* 抽屉的焦点：打开时记下触发它的控件并把焦点移进抽屉，关闭时还回去（控件每次渲染都会重建，所以靠 data-seq 找回时间线上的卡片） */
let drawerOpener = null, drawerWantsFocus = false;
function rememberOpener() {
  const active = document.activeElement;
  if (active && active !== document.body && !$("detail").contains(active)) drawerOpener = active;
  drawerWantsFocus = true;
}
function openDetail(seq) { rememberOpener(); state.selected = seq; state.cited = ""; state.follow = false; requestRender(); }
function openCite(id) { rememberOpener(); state.selected = 0; state.cited = id; state.follow = false; requestRender(); }
function closeDetail() {
  const seq = state.selected, id = state.cited;
  state.selected = 0; state.cited = "";
  const back = drawerOpener; drawerOpener = null; drawerWantsFocus = false;
  requestRender();
  setTimeout(() => {        // 等这一轮渲染落定后再还焦点
    let target = back && back.isConnected ? back : null;
    if (!target && seq) target = document.querySelector('.ev[data-seq="' + seq + '"]');
    if (!target && id) {    // 引用徽章每帧重建，记下的那个必已失效：找回同编号的一个徽章
      target = [...document.querySelectorAll(".chip")].find((chip) => chip.textContent === id) || null;
    }
    if (target) target.focus();
  }, 200);
}

function citedByText(entries) {                     // 谁引用了它：角色（中文名）· 黑板键 · 事件号
  return entries.map((entry) => {
    const lane = LANES.find(([key]) => key === entry.actor);
    return (lane ? lane[1] : entry.actor || "系统") + "（" + entry.key + "，#" + entry.seq + "）";
  }).join("；");
}

const EVENT_KIND_TEXT = { "run.started": "运行", "run.finished": "运行", "model.called": "模型调用",
                          "model.failed": "模型调用", "gate.checked": "独立审计", "artifact.written": "产物",
                          "route": "路由", "budget.exceeded": "预算" };

function setDrawerHead(type, title) {               // 抬头（WP56 照原型）：眉标是对象类型，标题在下
  const kind = $("detail-type"), heading = $("detail-title");
  if (kind) kind.textContent = type;
  if (heading) heading.textContent = title;
}

function renderCite(model, box) {                   // 引用徽章点开的面板：事实或条目本身（WP48 换成完整证据面板）
  const id = state.cited, fact = model.facts[id], item = model.items[id];
  setDrawerHead("引用", id);
  const rows = [["编号", id]];
  const kids = [];
  if (fact) {
    rows.push(["种类", "事实（" + (FACT_KIND_TEXT[fact.kind] || fact.kind) + "）"],
              ["数值", withUnit(fact.value, fact.unit)], ["含义", fact.label || "—"],
              ["数据日", fact.as_of || "—"], ["来源", fact.source || "—"]);
  } else if (item) {
    rows.push(["种类", "条目（" + (ITEM_KIND_TEXT[item.kind] || item.kind) + "）"],
              ["内容", item.title || item.text || item.question || item.lesson || "—"],
              ["发布日", item.published || item.end_date || item.as_of || item.known_on || "—"],
              ["来源", item.source || item.ref || "—"]);
  } else {
    kids.push(el("dl", { class: "kv" }, [el("dt", null, "编号"), el("dd", null, id)]),
              el("p", { class: "note" }, "未知引用：这次运行里没有这个编号对应的事实或条目。"));
    fill(box, kids);
    if (citePanelExtra) citePanelExtra(model, box);   // 挂点（00-core 声明）：未知引用要不要补，由挂点自己判断
    return;
  }
  const by = model.cited_by[id];
  kids.push(el("dl", { class: "kv" }, rows.map(([k, v]) => [el("dt", null, k), el("dd", null, v)])));
  kids.push(el("h3", null, "谁引用了它"), el("p", { class: "note" },
    by && by.length ? citedByText(by) : "没有角色在产物里引用过它。"));
  fill(box, kids);
  if (citePanelExtra) citePanelExtra(model, box);     // 挂点（00-core 声明，§7 共享文件请求）：40-evidence 接「在证据中查看」
}

function renderDetail(model) {
  const box = $("detail"), scrim = $("drawer-scrim");
  const hadFocus = box.contains(document.activeElement);   // 抽屉每次渲染都重建：焦点在里面时，重建后放回关闭键
  renderDetailBody(model, $("detail-body"));
  box.hidden = state.cited ? false : !state.events.some((event) => event.seq === state.selected);
  if (scrim) scrim.hidden = box.hidden;
  const close = box.hidden ? null : $("detail-close");
  if (close && (drawerWantsFocus || hadFocus)) { close.focus(); drawerWantsFocus = false; }
}

function renderDetailBody(model, body) {
  if (state.cited) { renderCite(model, body); return; }
  const e = state.events.find((event) => event.seq === state.selected);
  if (!e) { fill(body); return; }
  const d = e.data, lane = LANES.find(([key]) => key === e.actor);
  setDrawerHead(EVENT_KIND_TEXT[e.type] || "事件", describe(e)[0]);
  const pairs = [["事件", "#" + e.seq + " · " + e.type], ["执行者", (lane ? lane[1] : e.actor || "系统") + (e.step ? "（" + e.step + "）" : "")]];
  if (e.ts) pairs.push(["时间", e.ts]);
  if (e.caused_by && e.caused_by.length) pairs.push(["依据的事件", e.caused_by.map((seq) => "#" + seq).join(" ")]);
  const kids = [];
  const json = (value) => el("pre", null, JSON.stringify(value, null, 2));

  if (e.type === "model.called") {
    pairs.push(["模型", d.model || "—"], ["请求次数", d.usage.requests + (d.usage.requests > 1 ? "（含纠正重试）" : "")],
      ["token", "输入 " + count(d.usage.input_tokens) + "，输出 " + count(d.usage.output_tokens)],
      ["成本", d.cost === null || d.cost === undefined ? "未定价" : num(d.cost, 6)]);
    kids.push(el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", null, k), el("dd", null, v)])),
      el("h3", null, "提示词：这个角色能看到的全部内容"), el("pre", null, d.prompt || ""),
      el("h3", null, d.output === null ? "回复：纠正一次后仍不合契约，没有产物" : "回复：通过契约校验的输出"),
      d.output === null ? null : json(d.output),
      el("details", null, el("summary", null, "完整往来（模型库记录的每一条消息）"), json(d.messages)));
  } else if (e.type === "gate.checked") {
    pairs.push(["闸门", STAGE_NAME[d.gate] || d.gate], ["结论", d.findings ? "发现 " + d.findings + " 个问题" : "通过"]);
    kids.push(el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", null, k), el("dd", null, v)])));
    if (d.detail && d.detail.length) {
      kids.push(el("ul", { class: "findings" }, d.detail.map((item) => el("li", null,
        el("span", { class: "mono" }, "[" + item.check + "] " + (item.subject || "")),
        item.owner ? "（修改人：" + (STAGE_NAME[item.owner] || item.owner) + "）" : "", el("br"), item.message || ""))));
    }
  } else if (e.type === "artifact.written") {
    pairs.push(["黑板键", d.key], ["版本", "第 " + d.version + " 版"]);
    kids.push(el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", null, k), el("dd", null, v)])), json(d.value));
  } else {
    if (e.type === "route") pairs.push(["重跑", "从" + (STAGE_NAME[d.to] || d.to) + "开始，以及它之后的每一段"], ["原因", d.why || ""]);
    kids.push(el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", null, k), el("dd", null, v)])), json(d));
  }
  fill(body, kids);
}

/* ---------- 回放 ---------- */

const complete = () => state.ended && state.shown >= state.events.length;

function schedule() {
  clearTimeout(state.timer);
  if (!state.playing) return;
  if (state.speed === "instant") {                // 瞬间：到了就揭示
    state.shown = state.events.length;
    if (state.ended) state.playing = false;
    requestRender();
    return;
  }
  state.timer = setTimeout(() => {
    if (state.shown < state.events.length) state.shown += 1;
    if (complete()) state.playing = false;
    requestRender();
    schedule();
  }, PACE[state.speed]);
}

function play() {
  if (complete()) state.shown = 0;
  state.playing = true; state.follow = true;
  requestRender(); schedule();
}
function pause() { state.playing = false; clearTimeout(state.timer); requestRender(); }

$("btn-play").addEventListener("click", () => (state.playing ? pause() : play()));
$("btn-step").addEventListener("click", () => {
  pause();
  if (state.shown < state.events.length) { state.shown += 1; state.follow = true; requestRender(); }
});
$("speeds").addEventListener("click", (ev) => {
  const speed = ev.target.dataset && ev.target.dataset.speed;
  if (!speed) return;
  state.speed = speed;
  if (speed === "instant" && !state.playing) { state.shown = state.events.length; state.follow = true; }
  requestRender(); schedule();
});
$("slider").addEventListener("input", (ev) => {
  state.playing = false; clearTimeout(state.timer);
  state.shown = Number(ev.target.value); state.follow = true;
  requestRender();
});
$("lanes").addEventListener("wheel", () => { state.follow = false; }, { passive: true });
const scrim = $("drawer-scrim");
if (scrim) scrim.addEventListener("click", closeDetail);   // 原型的抽屉遮罩：点遮罩等于关闭
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && (state.selected || state.cited)) closeDetail(); });

function renderEvents(model, box) { renderTimeline(model); }

registerView({ id: "events", title: "原始事件", render: renderEvents });
