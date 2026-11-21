/* 20-shell.js —— 页头、页签、抽屉与渲染循环（BLUEPRINT 20.9 的布局）。
   render 每帧至多跑一次：只画可见页签与总在渲染的部分。 */

/* ---------- 页头 ---------- */

function renderHeader(model) {
  const started = model.started;
  const badge = $("badge");
  if (!started) {
    $("title").textContent = "多智能体交易辅助系统";
    $("subtitle").textContent = "数字由代码计算并复算，方案由代码按规则审计，模型只负责判断。";
    document.title = "InsightCrew";
    if (state.meta) {
      badge.hidden = false;
      badge.className = "badge " + state.meta.mode;
      badge.textContent = state.meta.mode === "model"
        ? "真实模型 · " + state.meta.models.quick + " / " + state.meta.models.deep
        : "离线演示 · 脚本化模型（回复为预先编写）";
    }
    return;
  }
  const board = model.board, request = started.request || {};
  const scope = board["request/scope"] || {}, instrument = board["ledger/instrument"] || {};
  const ticker = instrument.ticker || scope.ticker || request.ticker || "";
  $("title").textContent = ticker + (instrument.name ? " · " + instrument.name : "");
  $("subtitle").textContent = "分析日 " + (scope.as_of || request.as_of || "最近交易日")
    + " · 数据集 " + (started.universe || "") + (state.runId ? " · 运行 " + state.runId : "");
  document.title = ticker + " · InsightCrew";
  const names = started.models || {};
  badge.hidden = false;
  badge.className = "badge " + started.mode;
  badge.textContent = started.mode === "scripted" ? "离线演示 · 脚本化模型（回复为预先编写）"
    : (started.mode === "replay" ? "回放 · " : "真实模型 · ") + names.quick + " / " + names.deep;
}

/* ---------- 页签 ---------- */

function selectTab(id) {
  if (!$("view-" + id)) return;
  state.tab = id;
  for (const button of $("tabs").querySelectorAll("button")) {
    const on = button.dataset.view === id;
    button.classList.toggle("on", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
    button.tabIndex = on ? 0 : -1;               // 页签组只占一个 Tab 停靠点，组内用方向键移动
  }
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== "view-" + id;
  requestRender();
}

/* ---------- 渲染循环 ---------- */

let renderFrame = 0, renderTimer = 0;
function requestRender() {
  /* 一帧至多渲染一次：合并成一次动画帧回调。隐藏的页签会冻结 rAF
     （内嵌浏览器、后台标签都是这样），所以兜一个短定时器，页面不可见时也能画。 */
  if (renderFrame || renderTimer) return;
  const done = () => { renderFrame = 0; renderTimer = 0; render(); };
  renderFrame = requestAnimationFrame(() => { clearTimeout(renderTimer); done(); });
  renderTimer = setTimeout(() => { cancelAnimationFrame(renderFrame); done(); }, 150);
}

function render() {
  const model = fold(view(), state.trace, state.live);   // 一次渲染只折一次，每个视图从这里画
  const banner = $("banner");
  banner.hidden = !state.error;
  const bannerKey = state.error ? state.error + "|" + state.runId : "";
  if (banner.dataset.key !== bannerKey) {        // 只在内容变了才重画：role=alert 每次重画都会再播报一遍
    banner.dataset.key = bannerKey;
    fill(banner, state.error ? [
      "这次运行没有正常结束：" + state.error + "。这是程序的问题，不是分析结论。",
      state.live && state.runId
        ? el("button", { class: "ctl banner-retry", type: "button", on: { click: () => openRun(state.runId, false) } }, "重试")
        : null] : []);
  }
  renderHeader(model); renderRunState(model); renderPipeline(model); announceStatus();
  for (const v of VIEWS)
    if (v.always || v.id === state.tab) v.render(model, $(v.target || "view-" + v.id));
  renderDetail(model);
  $("playbar").hidden = !state.events.length;    // 回放条在工作台顶部（WP54）：有事件才出现
  $("btn-play").textContent = state.playing ? "暂停" : complete() ? "重新播放" : "播放";
  $("btn-play").classList.toggle("on", state.playing);
  for (const button of $("speeds").querySelectorAll("button")) button.classList.toggle("on", button.dataset.speed === state.speed);
  $("slider").max = String(state.events.length);
  $("slider").value = String(state.shown);
  $("progress").textContent = state.shown + " / " + state.events.length + (state.ended ? "" : " …");
  renderReplayMarks();
}

/* 读屏：运行状态变化时播报一次（胶囊文字），不随每帧重画而重复 */
let announced = "";
function announceStatus() {
  const pill = $("run-state"), text = pill.hidden ? "" : pill.textContent;
  if (text === announced) return;
  announced = text;
  $("sr-status").textContent = text ? "运行状态：" + text : "";
}

$("tabs").addEventListener("keydown", (ev) => {
  const tabs = [...$("tabs").querySelectorAll("button")], at = tabs.indexOf(document.activeElement);
  if (at < 0) return;
  const to = { ArrowRight: (at + 1) % tabs.length, ArrowLeft: (at + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[ev.key];
  if (to === undefined) return;
  ev.preventDefault();
  selectTab(tabs[to].dataset.view);
  tabs[to].focus();
});

$("tabs").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (button && button.dataset.view) selectTab(button.dataset.view);
});
