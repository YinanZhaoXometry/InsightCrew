/* 90-boot.js —— 状态、事件流与入口。 */

const state = {
  live: false,        // 实时模式（由本地服务提供页面）还是静态模式（事件嵌在页面里）
  meta: null,         // 实时模式下表单需要的信息
  runId: "",
  events: [],         // 收到的全部事件，按 seq
  shown: 0,           // 已揭示的条数：回放的位置
  ended: true,        // 事件是否已经到齐
  error: null,        // 事件流以 error 收尾时的说明
  trace: [],          // 耗时轨迹（静态模式嵌在页面里；实时模式等事件流送上，WP45）
  bars: null, currency: "", barsAsked: false,
  playing: false, speed: "normal", timer: null,
  source: null,
  selected: 0,        // 详情里打开的事件的 seq
  cited: "",          // 详情里打开的引用编号（cite 徽章点开的）
  follow: true,       // 时间线是否跟着最新一条滚动
  tab: "process",     // 可见的页签
};

const view = () => state.events.slice(0, state.shown);   // 已揭示的事件：回放的位置

/* ---------- 实时模式：表单、过去的运行、事件流 ---------- */

async function getJSON(path, options) {
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch (_) { data = null; }
  return { status: response.status, data };
}

function resetRun(runId) {
  if (state.source) state.source.close();
  clearTimeout(state.timer);
  Object.assign(state, { runId, events: [], shown: 0, ended: !runId, error: null, trace: [], bars: null, currency: "",
                         barsAsked: false, playing: false, source: null, selected: 0, cited: "", follow: true });
}

function openRun(runId, watch) {
  /* 打开一次运行并接上它的事件流。watch 为真时按阅读速度逐条揭示，否则到了就显示。 */
  resetRun(runId);
  const wanted = runId ? "?run=" + encodeURIComponent(runId) : window.location.pathname;
  if (window.location.search !== (runId ? wanted : "")) window.history.pushState(null, "", wanted);
  if (!runId) { $("history").open = true; requestRender(); return; }
  $("history").open = false;                        // 列表可能很长：看一次运行时收起来，别把运行挤到页面外
  state.speed = watch ? "normal" : "instant";
  state.follow = watch;                             // 看现场时跟着最新一栏走；翻看旧运行时从头看起
  state.playing = true;
  const source = new EventSource("/api/runs/" + encodeURIComponent(runId) + "/events");
  state.source = source;
  source.addEventListener("journal", (message) => {
    const event = JSON.parse(message.data);
    if (state.events.length && event.seq <= state.events[state.events.length - 1].seq) return;
    state.events.push(event);
    if (!state.barsAsked && event.type === "artifact.written" && event.data.key === "request/scope") loadBars(runId);
    if (state.playing && state.speed === "instant") state.shown = state.events.length;
    requestRender();
  });
  source.addEventListener("end", (message) => {     // 收尾消息之后必须关闭，否则浏览器会重连一个已结束的运行
    source.close();
    state.ended = true;
    state.error = JSON.parse(message.data).error;
    if (state.playing && state.speed === "instant") { state.shown = state.events.length; state.playing = false; }
    requestRender();
    loadHistory();
  });
  source.onerror = () => {
    if (source.readyState !== EventSource.CLOSED || state.ended) return;      // 其余情况浏览器会自己重连
    state.ended = true; state.playing = false;
    state.error = state.events.length ? "与本地服务的连接中断" : "没有这个运行";
    requestRender();
  };
  requestRender(); schedule();
}

async function loadBars(runId) {
  state.barsAsked = true;
  const { status, data } = await getJSON("/api/runs/" + encodeURIComponent(runId) + "/bars");
  if (status !== 200 || state.runId !== runId || !data.bars.length) return;
  state.bars = data.bars; state.currency = data.currency;
  requestRender();
}

async function startLive() {
  state.live = true;
  $("launch").hidden = false;
  const { status, data } = await getJSON("/api/meta");
  if (status !== 200) { $("mode-note").textContent = "读不到本地服务的信息。"; return; }
  state.meta = data;
  const scripted = data.mode !== "model";
  fill($("f-scenario"), el("option", { value: "" }, scripted ? "（不用场景）" : "（不用场景，调用真实模型）"),
    data.scenarios.map((s) => el("option", { value: s.name }, s.name + " · " + s.ticker + " @ " + s.as_of)));
  fill($("tickers"), data.instruments.map((i) => el("option", { value: i.ticker }, i.name)));
  $("f-judges").checked = data.defaults.judges;
  $("f-rounds").value = data.defaults.debate_rounds;
  $("mode-note").textContent = scripted
    ? "当前没有可用的真实模型，回复来自预先编写的场景。不选场景时，只有被准入拒绝的请求（例如 THIN、STALE）能走完，其余会在第一次模型调用处以 REVIEW 结束。"
    : "真实模型：quick = " + data.models.quick + "，deep = " + data.models.deep + "。每次运行都会调用模型并产生费用。";
  if (scripted && data.scenarios.some((s) => s.name === "demo")) $("f-scenario").value = "demo";
  applyScenario();
  $("f-scenario").addEventListener("change", applyScenario);
  $("form").addEventListener("submit", submitForm);
  window.addEventListener("popstate", () => openRun(new URLSearchParams(window.location.search).get("run") || "", false));
  loadHistory();
  const runId = new URLSearchParams(window.location.search).get("run") || "";
  if (runId) openRun(runId, false); else { $("history").open = true; requestRender(); }
}

/* ---------- 入口：静态内嵌，或实时 ---------- */

const embedded = JSON.parse($("embedded").textContent);
if (embedded) {
  state.events = embedded.events;
  state.bars = embedded.bars;
  state.trace = embedded.trace || [];
  state.shown = state.events.length;              // 打开就是最终状态；按"重新播放"从头看一遍
  state.speed = "normal";
  state.follow = false;
  selectTab(state.tab);          // 选中的页签与第一次渲染
} else {
  selectTab(state.tab);
  startLive();
}
