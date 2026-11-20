/* 55-launch.js —— 发起运行的表单与过去的运行（第一步拆分：暂为原有代码；WP50 接手）。 */

async function loadHistory() {
  const { status, data } = await getJSON("/api/runs");
  const body = $("history-body");
  if (status !== 200) { body.textContent = "读不到运行列表。"; return; }
  if (!data.runs.length) { body.textContent = "还没有运行。"; return; }
  const when = (id) => id.slice(0, 4) + "-" + id.slice(4, 6) + "-" + id.slice(6, 8) + " "
    + id.slice(9, 11) + ":" + id.slice(11, 13) + ":" + id.slice(13, 15);
  fill(body, table(["开始时间", "代码", "分析日", "模型", "结果", "原因", ""], data.runs.map((run) => [
    when(run.run_id), run.ticker, run.as_of, MODE_TEXT[run.mode] || run.mode,
    el("span", { class: run.status === "delivered" ? "pass" : run.status === "review" ? "fail" : "na" },
       STATUS_TEXT[run.status] || run.status),
    run.reason === "ok" ? "" : reasonText(run.reason),
    el("button", { class: "linkish", type: "button", on: { click: () => { openRun(run.run_id, false); window.scrollTo(0, 0); } } }, "查看")])));
}

function applyScenario() {
  const name = $("f-scenario").value;
  const scenario = state.meta.scenarios.find((s) => s.name === name);
  const note = $("scenario-note");
  note.hidden = !scenario;
  for (const id of ["f-judges", "f-rounds", "f-risk-rounds", "f-tools", "f-memory", "f-channels"])
    $(id).disabled = Boolean(scenario);   // 场景自带流程配置（组合文件仍然可以给：--portfolio 优先于场景内联）
  if (!scenario) return;
  $("f-ticker").value = scenario.ticker;
  $("f-date").value = scenario.as_of;
  note.textContent = "场景 " + scenario.name + "：" + scenario.about + " 评审与辩论轮数用场景自己的配置。";
}

async function submitForm(ev) {
  ev.preventDefault();
  const error = $("form-error");
  error.hidden = true;
  const body = {}, scenario = $("f-scenario").value, ticker = $("f-ticker").value.trim(), day = $("f-date").value;
  if (scenario) body.scenario = scenario;
  if (ticker) body.ticker = ticker;
  if (day) body.as_of = day;
  const portfolio = $("f-portfolio").value.trim();
  if (portfolio) body.portfolio = portfolio;
  if (!scenario) {
    body.judges = $("f-judges").checked;
    if ($("f-rounds").value !== "") body.debate_rounds = Number($("f-rounds").value);
    if ($("f-risk-rounds").value !== "") body.risk_rounds = Number($("f-risk-rounds").value);
    if ($("f-tools").checked) body.tools = true;
    if ($("f-memory").checked) body.memory = true;
    const channels = [...$("f-channels").selectedOptions].map((option) => option.value);
    if (channels.length) body.channels = channels;
  }
  const { status, data } = await getJSON("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" },
                                                        body: JSON.stringify(body) });
  if (status === 202) { openRun(data.run_id, true); return; }
  error.hidden = false;
  fill(error, "没有开始：" + ((data && data.error) || "请求不合格式（HTTP " + status + "）") + "。",
    data && data.active_run ? el("button", { class: "linkish", type: "button",
      on: { click: () => openRun(data.active_run, false) } }, "查看正在进行的运行") : null);
}
