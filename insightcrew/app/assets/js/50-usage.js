/* 50-usage.js —— 用量与耗时页签（WP44 放到位；WP49 接手）。 */

const costText = (row) => row.unpriced === row.calls ? "未定价"
  : num(row.cost, 6) + (row.unpriced ? "（另有未定价）" : "");

/* ---------- 用量 ---------- */

function renderUsage(model, box) {
  const kids = [el("h2", null, "用量与成本")];
  const total = model.usage.total;
  if (!total.calls) { fill(box, kids, el("div", { class: "muted" }, "还没有模型调用。")); return; }
  const ceilings = (model.started || {}).ceilings || {}, roleCaps = ceilings.roles || {};
  const groups = new Map();                          // 按角色累计的用量私推出角色组小计
  for (const [actor, row] of Object.entries(model.usage.roles)) {
    const key = actor.split(".")[0];
    if (!groups.has(key)) groups.set(key, { calls: 0, requests: 0, input: 0, output: 0, cost: 0, unpriced: 0 });
    const cell = groups.get(key);
    for (const field of ["calls", "requests", "input", "output", "cost", "unpriced"]) cell[field] += row[field];
  }
  const rows = [...groups].map(([key, row]) => [GROUP_NAME[key] || key, count(row.calls), count(row.requests),
    count(row.input), count(row.output), count(row.input + row.output),
    roleCaps[key] ? count(roleCaps[key]) : "—", costText(row)]);
  const sum = ["合计", count(total.calls), count(total.requests), count(total.input), count(total.output),
               count(total.input + total.output), ceilings.run_tokens ? count(ceilings.run_tokens) : "不设上限", costText(total)];
  sum.cls = "total";
  kids.push(table(["角色组", "调用", "请求", "输入 token", "输出 token", "token 合计", "token 上限", "成本"],
                  [...rows, sum], [1, 2, 3, 4, 5, 6, 7]));
  if (ceilings.run_tokens) {
    const used = total.input + total.output, bar = el("i");
    bar.style.width = Math.min(100, 100 * used / ceilings.run_tokens) + "%";
    kids.push(el("div", { class: "meter" + (used > ceilings.run_tokens ? " over" : "") }, bar));
  }
  const notes = ["“请求”多于“调用”的部分是输出不合契约后的纠正重试。",
                 "成本上限：" + (ceilings.run_cost ? num(ceilings.run_cost, 6) : "不设上限") + "。"];
  const wall = model.board["run/wall_ms"];
  if (wall) notes.push("各段用时（毫秒）：" + Object.entries(wall)
    .map(([node, ms]) => (STAGE_NAME[node] || node) + " " + count(ms)).join("、") + "。");
  const exceeded = model.events.find((e) => e.type === "budget.exceeded");
  if (exceeded) notes.unshift("预算在第 " + exceeded.seq + " 条事件处超限（范围 " + exceeded.data.scope + "），此后不再发起模型调用。");
  kids.push(el("p", { class: "note" }, notes.join(" ")));
  fill(box, kids);
}

registerView({ id: "usage", title: "用量与耗时", render: renderUsage });
