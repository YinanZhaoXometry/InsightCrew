/* 45-audit.js —— 规则与审计页签（WP48，BLUEPRINT 20.10「Rules and audit」）。
   风控规则的结果表：规则的中文名、公式（只在已交付的运行里出现，与报告一致）、界限、
   计算值、通过与不通过、以及每条规则的说明；然后是审计的轮次：每一版方案的发现、
   流程回到哪一段、哪一版修好了它。核对分层 L1 与 L2 在此点名（连同发现会携带的 L3）。
   纯推导（auFindingShape / auRuleRows / auRoundCards / auProposalLine）可被 Node 测试直接加载。 */

/* ---------- 私有标签 ---------- */

const LAYER_NAME = { L1: "L1 上下文", L2: "L2 数字与引用", L3: "L3 规则" };   // 核对分层（蓝图 6.8）
const LAYER_TEXT = {                     // 发现只携带 L2 与 L3；L1 由构造保证，没有发现
  L1: "每个角色只看到自己的黑板视图，由代码构造保证",
  L2: "审计核对每个论断引用的编号与数值；每条事实由两条计算路径复算",
  L3: "规则手册的每条失败汇总成发现，交交易员修订",
};
const AU_STATUS = { pass: ["通过", "pass"], fail: ["不通过", "fail"], "n/a": ["不适用", "na"] };

/* ---------- 纯推导（Node 测试直接调） ---------- */

function auLabelOf(check) {
  /* 检查项或规则键 → 中文标签；两处都没有才回落原文，绝不空白 */
  return CHECK_LABEL[check] || RULE_LABEL[check] || check || "";
}

function auFindingShape(finding) {
  /* 一条发现：层级、检查项的中文名、对象、修改人、说明、界限与实际值（有才显示） */
  const owner = finding.owner || "";
  return {
    layer: finding.layer || "",
    check: finding.check || "",
    label: auLabelOf(finding.check),
    subject: finding.subject || "",
    message: finding.message || "",
    owner, ownerText: STAGE_NAME[owner] || GROUP_NAME[owner] || owner,
    limit: finding.limit === undefined ? null : finding.limit,
    actual: finding.actual === undefined ? null : finding.actual,
  };
}

function auProposalLine(value, currency) {
  /* 一版方案的紧凑一行：操作与价位（null 是「未给出」，不是 0） */
  const v = value || {};
  const price = (x) => x === null || x === undefined ? "—" : num(x) + (currency ? " " + currency : "");
  return [ "操作 " + (v.action || "—"),
           "入场 " + price(v.entry),
           "止损 " + price(v.stop),
           "目标 " + price(v.target),
           "仓位 " + (v.size_pct === null || v.size_pct === undefined ? "—" : num(v.size_pct) + "%"),
           "持有 " + (v.horizon_days === undefined ? "—" : v.horizon_days) + " 个交易日" ].join("，");
}

function auRuleRows(model, delivered) {
  /* 规则表：中文标签、公式（仅已交付，与报告同一规矩）、界限、计算值、结论、说明 */
  const specs = {};
  for (const spec of model.board["rules/limits"] || []) specs[spec.key] = spec;
  return (model.board["audit/rules"] || []).map((rule) => ({
    key: rule.key || "",
    label: RULE_LABEL[rule.key] || ((specs[rule.key] || {}).desc) || rule.key || "",
    formula: delivered ? ((specs[rule.key] || {}).formula) || "" : "",
    limit: rule.limit === undefined || rule.limit === null ? null : rule.limit,
    actual: rule.actual === undefined || rule.actual === null ? null : rule.actual,
    status: rule.status || "",
    message: rule.message || "",
  }));
}

function auRoundCards(model) {
  /* 审计的轮次：方案的每一版 → 检查它的那次审计（发现取日志里那条 audit/findings 产物，
     它带 gate.detail 没有的 layer 与 limit/actual）、打回去向、修正它的那一版 */
  const rounds = model.rounds;
  const artifacts = model.versions["audit/findings"] || [];
  const finished = model.finished;
  const cards = [];
  for (let i = 0; i < rounds.length; i += 1) {
    const round = rounds[i];
    const gate = round.gate;
    const card = {
      version: round.version, seq: round.seq, by: round.by || "trader",
      proposal: round.value || {},
      checked: Boolean(gate), gateSeq: gate ? gate.seq : 0,
      pass: Boolean(gate) && !gate.data.findings,
      findings: [], routeTo: "", routeText: "", fixedBy: "", finalPass: false,
    };
    if (gate) {
      const source = artifacts.filter((a) => a.seq < gate.seq).pop();   // 闸门前最近一次审计落账的发现
      const list = source && Array.isArray(source.value) ? source.value : (gate.data.detail || []);
      card.findings = list.map(auFindingShape);
    }
    if (round.route) {
      card.routeTo = round.route.data.to || "";
      card.routeText = "打回：从「" + (STAGE_NAME[card.routeTo] || card.routeTo) + "」段重跑"
        + (round.route.data.why ? "（" + round.route.data.why + "）" : "");
    }
    const next = rounds[i + 1] || null;
    if (card.pass) {
      card.finalPass = i === rounds.length - 1 && model.status === "delivered";
    } else if (next) {
      card.fixedBy = "由第 " + next.version + " 版方案修正（事件 #" + next.seq + "）";
    } else if (finished && finished.status !== "delivered") {
      card.fixedBy = "未修正：运行以「" + reasonText(finished.reason) + "」结束，这一版不是结论";
    } else if (finished) {
      card.fixedBy = "未修正：运行已结束";
    } else {
      card.fixedBy = "等待交易员重写……";
    }
    cards.push(card);
  }
  return cards;
}

/* ---------- 规则与审计页签 ---------- */

function auRuleTable(model, delivered) {
  const rows = auRuleRows(model, delivered);
  const header = delivered ? ["规则", "公式", "界限", "计算值", "结论", "说明"]
                           : ["规则", "界限", "计算值", "结论", "说明"];   // 公式只进已交付的运行
  return table(header, rows.map((row) => {
    const [text, cls] = AU_STATUS[row.status] || [row.status, ""];
    const cells = delivered ? [
      [el("div", null, row.label), el("div", { class: "mono muted" }, row.key)],
      row.formula,
    ] : [
      [el("div", null, row.label), el("div", { class: "mono muted" }, row.key)],
    ];
    cells.push(row.limit === null ? "—" : num(row.limit),
               row.actual === null ? "—" : num(row.actual),
               el("span", { class: cls }, text),
               row.message);
    return cells;
  }), delivered ? [2, 3] : [1, 2]);
}

function auFindingsList(findings) {
  return el("ul", { class: "findings" }, findings.map((f) => el("li", null,
    f.layer ? el("span", { class: "layer " + f.layer.toLowerCase() }, f.layer) : null, " ",
    el("span", { class: "mono" }, "[" + f.label + "] " + f.subject),
    f.owner ? "（修改人：" + f.ownerText + "）" : "", el("br"),
    f.message || "",
    f.limit !== null || f.actual !== null
      ? el("span", { class: "muted" }, "（界限 " + num(f.limit) + "，实际 " + num(f.actual) + "）") : null)));
}

function renderAudit(model, box) {
  const kids = [el("h2", null, "规则与审计")];
  if (!model.events.length) {
    fill(box, ...kids, el("div", { class: "muted" },
      "还没有运行。从上方发起一次运行，或从「过去的运行」里打开一次。"));
    return;
  }
  const board = model.board, outcome = board["run/outcome"];
  const delivered = outcome && outcome.status === "delivered";
  kids.push(el("p", { class: "note" }, "核对分层（L1 与 L2 在此点名；发现携带自己的层级）："),
    el("ul", { class: "layers" },
      el("li", null, el("b", { class: "layer L1" }, LAYER_NAME.L1), "——" + LAYER_TEXT.L1 + "。"),
      el("li", null, el("b", { class: "layer L2" }, LAYER_NAME.L2), "——" + LAYER_TEXT.L2 + "。"),
      el("li", null, el("b", { class: "layer L3" }, LAYER_NAME.L3), "——" + LAYER_TEXT.L3 + "。")),
    el("p", { class: "note" }, "评审是 L4 判断：只能否决，不能证明方案正确；逐条意见在「过程」页签。"));

  /* 规则表 */
  const rules = board["audit/rules"];
  if (rules && rules.length) {
    kids.push(el("h3", null, "风控规则（计算值由代码算出，模型不提供任何派生数字）"),
      el("div", { class: "ev-scroll" }, auRuleTable(model, delivered)),
      el("p", { class: "note" }, delivered
        ? "公式一栏照录规则手册的文字，永远不会被求值；界限与计算值都来自代码。"
        : "这次运行没有交付结论：与报告一样，公式一栏不出现（有一条规则的公式写着评级的名字）。"));
  } else {
    kids.push(el("h3", null, "风控规则"), el("p", { class: "note" },
      model.status === "running" ? "运行进行中：审计还没有检查过方案。"
        : "（没有规则结果：这次运行没有走到审计，或审计没有方案可查。）"));
  }

  /* 审计的轮次 */
  const cards = auRoundCards(model);
  kids.push(el("h3", null, "审计的轮次"));
  if (!cards.length) {
    kids.push(el("p", { class: "note" }, "（还没有方案提交审计。）"));
  } else {
    kids.push(el("p", { class: "note" },
      "交易员的每一版方案都由独立审计检查过；被否决的版本不是结论。按顺序列出每一轮的发现、流程的去向与修正它的那一版。"));
    const currency = (board["ledger/instrument"] || {}).currency || "";
    for (const card of cards) {
      const head = [el("b", null, "第 " + card.version + " 版方案"),
        el("span", { class: "mono muted" }, "#" + card.seq),
        el("span", { class: "rnd-line" }, auProposalLine(card.proposal, currency))];
      if (!card.checked) {
        head.push(el("span", { class: "na" }, "未检查"));
      } else if (card.pass) {
        head.push(el("span", { class: "pass" }, "审计通过"));
      } else {
        head.push(el("span", { class: "fail" }, "发现 " + card.findings.length + " 个问题"));
      }
      const body = [];
      if (!card.checked) body.push(el("p", { class: "note" }, "这一版还没有被审计检查：运行在审计之前结束或仍在途中。"));
      if (card.findings.length) body.push(auFindingsList(card.findings));
      if (card.routeText) body.push(el("p", { class: "note" }, card.routeText));
      if (!card.pass && card.fixedBy) body.push(el("p", { class: "note" }, card.fixedBy));
      if (card.finalPass) body.push(el("p", { class: "note" }, "这一版就是交付的方案。"));
      kids.push(el("div", { class: "rnd" }, el("div", { class: "rnd-head" }, ...head), ...body));
    }
  }
  fill(box, kids);
}

registerView({ id: "audit", title: "规则与审计", render: renderAudit });
