/* 26-screens.js —— 三屏切换与回放的事件标记（WP54，照主人的 OpenDesign 原型）。
   屏：empty（无运行欢迎页）/ setup（新建分析）/ work（工作台，#run）。
   实时页从 empty 起步，静态 replay.html 直接落在 work；setup 只在实时页出现。
   历史是屏外常驻的一节：work 与 empty 屏显示，setup 屏让位给表单。 */

const SCREENS = ["empty", "setup", "work"];

function showScreen(name) {
  if (!SCREENS.includes(name)) return;
  state.screen = name;
  $("screen-empty").hidden = name !== "empty";
  $("screen-setup").hidden = name !== "setup";
  $("run").hidden = name !== "work";
  $("history").hidden = !state.live || name === "setup";
  const button = $("btn-new");
  if (button) button.hidden = !state.live || name === "setup";
  const back = $("btn-back-work");
  if (back) fill(back, icon("arrow-left"), state.runId ? "返回工作台" : "返回首页");
  /* 预估面板的空态（原型的 .estimate-empty）：还没预估过就先说明两步预估怎么走 */
  const estimate = $("estimate");
  if (name === "setup" && estimate && typeof estimateReady !== "undefined"
      && !estimateReady && !estimate.querySelector(".est-cards")) {
    estimate.hidden = false;
    estimate.classList.remove("stale");
    fill(estimate, el("div", { class: "estimate-empty" }, icon("file"),
      el("p", null, "先看花费预估：在左侧选好标的与日期，点「查看花费预估」，这里会给出将运行的阶段、典型调用与数据覆盖；确认后才启动。")));
  }
  requestRender();
}

/* 空屏的两个入口与页头按钮：进 setup；演示键顺带把场景选成 demo（选不上就保持现状） */
function gotoSetup(demo) {
  showScreen("setup");
  if (demo) {
    const select = $("f-scenario");
    if (select && [...select.options].some((option) => option.value === "demo")) {
      select.value = "demo";
      if (typeof applyScenario === "function") applyScenario();
    }
  }
  window.scrollTo(0, 0);
}

/* ---------- 回放的事件标记：关键事件一颗点，点它跳到那一刻 ---------- */

/* 纯函数：一份事件里的关键事件（Node 测试直接调）。开始、每次打回、审计发现问题、
   预算超限、模型失败、收尾——这些是会改变方案或结束运行的节点。 */
function marksFor(events) {
  const marks = [];
  for (const e of events) {
    let label = "";
    if (e.type === "run.started") label = "开始";
    else if (e.type === "route") label = "打回" + (STAGE_NAME[e.data.to] ? "→" + STAGE_NAME[e.data.to] : "");
    else if (e.type === "gate.checked") { if (e.data.findings > 0) label = "审计发现 " + e.data.findings; }
    else if (e.type === "budget.exceeded") label = "预算超限";
    else if (e.type === "model.failed") label = "调用失败";
    else if (e.type === "run.finished") label = e.data.status === "delivered" ? "交付" : "REVIEW";
    if (label) marks.push({ seq: e.seq, label });
  }
  return marks;
}

function renderReplayMarks() {
  const strip = $("replay-marks");
  if (!strip) return;
  const marks = marksFor(state.events);
  strip.hidden = !marks.length;
  fill(strip, marks.map((mark) => el("button", {
    class: "replay-mark" + (mark.seq <= state.shown ? " reached" : "")
      + (mark.seq === state.shown ? " current" : ""),
    type: "button", title: "#" + mark.seq + " " + mark.label, "data-seq": mark.seq,
    on: { click: () => {
      state.playing = false; clearTimeout(state.timer);
      state.shown = mark.seq; state.follow = true;
      requestRender();
    } } },
    el("i"), el("span", null, mark.label))));
}

/* 加载时接住三屏的固定按钮（静态页这些按钮所在屏不可见，接了也无妨） */
if (typeof document !== "undefined" && document.getElementById("embedded")) {
  const heroNew = $("btn-hero-new"), heroDemo = $("btn-hero-demo");
  if (heroNew) heroNew.addEventListener("click", () => gotoSetup(false));
  if (heroDemo) heroDemo.addEventListener("click", () => gotoSetup(true));
  const headerNew = $("btn-new");
  if (headerNew) headerNew.addEventListener("click", () => gotoSetup(false));
  const back = $("btn-back-work");
  if (back) back.addEventListener("click", () => showScreen(state.runId ? "work" : "empty"));
  /* 品牌回首页（WP57 owner 提的惯例）：任何屏点左上角 InsightCrew 都回欢迎页；
     openRun("") 会顺带清掉地址里的 ?run=。回车/空格也能触发（role=button）。 */
  const home = $("btn-home");
  if (home) {
    const goHome = () => openRun("");
    home.addEventListener("click", goHome);
    home.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); goHome(); }
    });
  }
}
