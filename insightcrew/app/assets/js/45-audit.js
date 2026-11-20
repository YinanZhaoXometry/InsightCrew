/* 45-audit.js —— 规则与审计页签：WP44 建空视图占位，WP48 填充正文（规则结果表与审计轮次）。
   眼下规则表与评审仍在「证据」页签里，随本包拆过来。 */

function renderAudit(model, box) {
  fill(box, el("h2", null, "规则与审计"),
    el("div", { class: "muted" },
      "这一页将展示风控规则的结果表（界限、计算值、通过与不通过），以及审计把方案打回的每一轮：发现了什么、流向了哪一段、哪一版修好了它。",
      el("br"), "由后续工作包（WP48）填充；规则表与评审眼下仍在「证据」页签里。"));
}

registerView({ id: "audit", title: "规则与审计", render: renderAudit });
