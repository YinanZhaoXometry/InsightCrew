/* 35-process.js —— 过程页签：WP44 建空视图占位，WP47 填充正文（辩论文本、修订对照、风险三方意见、评审）。 */

function renderProcess(model, box) {
  fill(box, el("h2", null, "过程"),
    el("div", { class: "muted" },
      "这一页将按阶段讲述整个运行：各分析师的论断与解读、多空辩论的交锋、方案被审计打回的修订对照、风险三方意见与评审意见。",
      el("br"), "由后续工作包（WP47）填充；眼下请看「原始事件」页签的逐条过程记录。"));
}

registerView({ id: "process", title: "过程", render: renderProcess });
