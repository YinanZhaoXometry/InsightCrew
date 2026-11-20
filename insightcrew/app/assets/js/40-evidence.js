/* 40-evidence.js —— 证据页签（WP44 放到位；WP48 接手并拆走规则与评审两节）。 */

/* ---------- 证据 ---------- */

function renderEvidence(model, box) {
  const board = model.board, outcome = board["run/outcome"], kids = [el("h2", null, "证据")];
  const delivered = outcome && outcome.status === "delivered";
  for (const [key, name] of [["fundamentals", "基本面"], ["disclosures", "公告"], ["news", "新闻"],
                             ["sentiment", "情感"], ["macro", "宏观"]]) {
    const status = board[key + "/status"];
    if (!status) continue;                       // 这次运行没开这条通道：一行都不加
    const text = (CHANNEL_STATE[key] || {})[status.state] || status.state;
    kids.push(el("p", { class: "note" },
      name + "通道：" + text + (status.state !== "available" && status.reason ? "（" + status.reason + "）" : "")));
  }
  const pstate = board["portfolio/state"];
  if (pstate) {                                  // 组合状态：没给组合文件时没有这个键
    const text = PSTATE_TEXT[pstate.state] || pstate.state;
    const held = pstate.state === "held" ? "数量 " + num(pstate.quantity) + "（" + (pstate.currency || "—")
        + "），占权益 " + num(pstate.position_pct) + "%" : "";
    const flat = pstate.state === "flat" ? "现金占权益 " + num(pstate.cash_pct) + "%"
        + "（" + (pstate.currency || "—") + "）" : "";
    kids.push(el("p", { class: "note" }, "组合状态：" + text
      + (held || flat || (pstate.reason ? "（" + pstate.reason + "）" : "")) + (held && pstate.cash_pct !== null
        ? "；现金占权益 " + num(pstate.cash_pct) + "%" : "")));
  }
  const fundFacts = board["fundamentals/facts"];
  if (fundFacts) {
    const factMap = Object.fromEntries(fundFacts.map((fact) => [fact.id, fact]));
    const claims = ((board["fundamentals/analysis"] || {}).claims) || [];
    if (claims.length) {
      kids.push(el("h3", null, "基本面事实（数值取自年报，经第二条路径复算）"),
        table(["事实", "含义", "申报值", "申报日", "分析师的解读"], claims.map((claim) => {
          const fact = factMap[claim.fact] || {};
          return [el("span", { class: "mono" }, claim.fact), fact.label || "（没有这个基本面事实）",
                  withUnit(fact.value, fact.unit), fact.as_of || "—", claim.reading || ""];
        }), [2]));
    }
    const card = board["fundamentals/card"];
    if (card) {
      const notes = ["来源 " + card.source + "，申报货币 " + card.currency + "，最近财年期末 "
        + card.fiscal_year_end + "，所引最晚申报 " + card.filed + "，" + card.rows + " 行"];
      if (card.unavailable && card.unavailable.length)
        notes.push("不可用：" + card.unavailable.map((u) => u.id + "（" + u.reason + "）").join("；"));
      if (card.warnings && card.warnings.length) notes.push("重述提醒：" + card.warnings.join("；"));
      kids.push(el("p", { class: "note" }, notes.join("。")));
    }
  }
  const items = board["disclosures/items"];
  if (items) {
    kids.push(el("h3", null, "公告条目（来源原文，未经核验）"),
      table(["编号", "发布日", "类别", "标题", "来源编号"], items.map((item) =>
        [el("span", { class: "mono" }, item.id), item.published, item.kind, item.title,
         el("span", { class: "mono" }, item.ref)])));
    const dAnalysis = board["disclosures/analysis"];
    if (dAnalysis) {
      if (dAnalysis.signals && dAnalysis.signals.length) {
        kids.push(el("ul", { class: "signals" }, dAnalysis.signals.map((signal) => el("li", null,
          el("span", { class: "mono" }, signal.item), " "
          + (DIRECTION_TEXT[signal.direction] || signal.direction) + "——" + signal.reading))));
      }
      kids.push(el("p", { class: "note" }, "窗口内公告合起来的基调："
        + (STANCE_TEXT[dAnalysis.stance] || dAnalysis.stance) + "（把握：" + dAnalysis.confidence + "）"));
    }
    const dcard = board["disclosures/card"];
    if (dcard) kids.push(el("p", { class: "note" }, "窗口 " + dcard.window_from + " 到 " + dcard.window_to
      + "，" + dcard.items + " 条；丢弃无日期 " + dcard.dropped + " 条，截断 " + dcard.truncated + " 条"));
  }
  const newsItems = board["news/items"];
  if (board["news/status"] && board["news/status"].state !== "unavailable") {
    kids.push(el("h3", null, "新闻标题（来源原文，未经核验）"));
    if (newsItems && newsItems.length) {
      kids.push(table(["编号", "发布日", "来源", "标题"], newsItems.map((item) =>
        [el("span", { class: "mono" }, item.id), item.published, item.source, item.title])));
    } else kids.push(el("p", { class: "note" }, "（窗口内没有新闻。）"));
    const nAnalysis = board["news/analysis"];
    if (nAnalysis) {
      if (nAnalysis.signals && nAnalysis.signals.length) {
        kids.push(el("ul", { class: "signals" }, nAnalysis.signals.map((signal) => el("li", null,
          el("span", { class: "mono" }, signal.item), " "
          + (DIRECTION_TEXT[signal.direction] || signal.direction) + "——" + signal.reading))));
      }
      kids.push(el("p", { class: "note" }, "窗口内新闻合起来的基调："
        + (STANCE_TEXT[nAnalysis.stance] || nAnalysis.stance) + "（把握：" + nAnalysis.confidence + "）"));
    }
    const ncard = board["news/card"];
    if (ncard) kids.push(el("p", { class: "note" }, "窗口 " + ncard.window_from + " 到 " + ncard.window_to
      + "，" + ncard.items + " 条；丢弃无日期 " + ncard.dropped + " 条，截断 " + ncard.truncated + " 条"));
  }
  if (board["macro/status"] && board["macro/status"].state !== "unavailable") {
    const macroFacts = board["macro/facts"] || [];
    if (macroFacts.length) {
      const readings = Object.fromEntries((((board["news/analysis"] || {}).claims) || [])
        .map((claim) => [claim.fact, claim.reading || ""]));
      kids.push(el("h3", null, "宏观事实（经两条路径复算，数值不取模型的转述）"),
        table(["事实", "含义", "数值", "数据日", "分析师的解读"], macroFacts.map((fact) =>
          [el("span", { class: "mono" }, fact.id), fact.label || "—", withUnit(fact.value, fact.unit),
           fact.as_of || "—", readings[fact.id] || "—"]), [2]));
    }
    const markets = board["macro/markets"] || [];
    if (markets.length) {
      kids.push(el("h3", null, "预测市场（来源原话，未经核验）"),
        el("p", { class: "note" }, "问题与概率照录自来源，程序不做核对；概率为来源给的隐含概率（0 到 1）。"),
        table(["编号", "问题", "隐含概率", "成交量", "截止日"], markets.map((m) =>
          [el("span", { class: "mono" }, m.id), m.question, num(m.probability), num(m.volume), m.end_date]), [2, 3]));
    }
  }
  if (board["sentiment/status"] && board["sentiment/status"].state !== "unavailable") {
    const sAnalysis = board["sentiment/analysis"];
    if (sAnalysis) {
      kids.push(el("h3", null, "社交情感"),
        el("p", { class: "note" }, "情绪带：" + (BAND_TEXT[sAnalysis.band] || sAnalysis.band)
          + "，情绪分 " + num(sAnalysis.score) + "（0 最看空，10 最看多）；把握：" + sAnalysis.confidence));
      if (sAnalysis.signals && sAnalysis.signals.length) {
        kids.push(el("ul", { class: "signals" }, sAnalysis.signals.map((signal) => el("li", null,
          el("span", { class: "mono" }, signal.post), " "
          + (DIRECTION_TEXT[signal.direction] || signal.direction) + "——" + signal.reading))));
      }
    }
    const posts = board["sentiment/posts"];
    if (posts && posts.length) {
      kids.push(table(["编号", "发布日", "来源", "自带标签", "内容"], posts.map((post) =>
        [el("span", { class: "mono" }, post.id), post.published, post.source,
         TAG_TEXT[post.tag] || post.tag, post.text])));
    } else kids.push(el("p", { class: "note" }, "（窗口内没有帖子。）"));
    const scard = board["sentiment/card"];
    if (scard) kids.push(el("p", { class: "note" }, "窗口 " + scard.window_from + " 到 " + scard.window_to
      + "，" + scard.items + " 条；丢弃无日期 " + scard.dropped + " 条，截断 " + scard.truncated + " 条"));
  }
  const lessons = board["memory/lessons"];
  if (lessons) {                                 // 记忆没开时没有这个键；开着而无可用的课程则是空表
    kids.push(el("h3", null, "用到的课程（只有结果可知日不晚于分析日的课程）"));
    kids.push(lessons.length
      ? table(["代码", "分析日", "结果可知于", "当时评级", "收益率", "超额收益", "触及", "课程"],
              lessons.map((lesson) => [lesson.ticker, lesson.as_of, lesson.known_on, lesson.rating,
                num(lesson.return_pct) + "%", lesson.alpha_pct === null || lesson.alpha_pct === undefined
                  ? "—" : num(lesson.alpha_pct) + "%", HIT_TEXT[lesson.hit] || lesson.hit, lesson.lesson]),
              [4, 5])
      : el("p", { class: "note" }, "（没有分析日之前已经知道结果的课程。）"));
  }
  const riskKeys = [["risk/aggressive", "激进风控"], ["risk/conservative", "保守风控"], ["risk/neutral", "中立风控"]];
  const riskRows = riskKeys.filter(([key]) => board[key]);
  if (riskRows.length) {
    kids.push(el("h3", null, "风险三方意见（未经审计的意见）"),
      el("p", { class: "note" }, "三位风控在审计之前评论方案；页面展示的价位与仓位是审计通过的那一版方案。"),
      table(["风控", "意见"], riskRows.map(([key, name]) => {
        const view = board[key] || {};
        return [name, delivered ? view.critique || "（没有批评）" : "（发表了意见；没有交付结论，正文见过程记录）"];
      })));
  }
  const analysis = board["technical/analysis"];
  const tools = board["tools/facts"] || [];
  if ((analysis && analysis.claims && analysis.claims.length) || tools.length) {
    const facts = Object.fromEntries((board["ledger/facts"] || []).map((fact) => [fact.id, fact]));
    const byTool = Object.fromEntries(tools.map((fact) => [fact.id, fact]));
    const claims = (analysis && analysis.claims) || [];
    const rows = claims.map((claim) => {
      const fact = facts[claim.fact] || byTool[claim.fact] || {};
      const fromTool = claim.fact in byTool;
      return [el("span", { class: "mono" }, claim.fact + (fromTool ? "（工具）" : "")),
              fact.label || (fromTool ? "（工具按需计算）" : "（账本中没有这个事实）"),
              withUnit(fact.value, fact.unit), fact.as_of || "—", claim.reading || ""];
    });
    const cited = new Set(claims.map((claim) => claim.fact));
    for (const fact of tools) if (!cited.has(fact.id)) {
      rows.push([el("span", { class: "mono" }, fact.id + "（工具）"), "（工具按需计算）",
                 withUnit(fact.value, fact.unit), fact.as_of || "—", "—"]);
    }
    kids.push(el("h3", null, "分析师引用的事实（数值取自事实账本，不取模型的转述；标有（工具）的行是按需请求的指标）"),
      table(["事实", "含义", "账本数值", "数据日", "分析师的解读"], rows, [2]));
  }
  const rules = board["audit/rules"];
  if (rules && rules.length) {
    const specs = Object.fromEntries((board["rules/limits"] || []).map((spec) => [spec.key, spec]));
    const status = { pass: ["通过", "pass"], fail: ["不通过", "fail"], "n/a": ["不适用", "na"] };
    kids.push(el("h3", null, "风控规则（计算值由代码算出）"),
      table(["规则", "含义", "界限", "计算值", "结论"], rules.map((rule) => {
        const [text, cls] = status[rule.status] || [rule.status, ""];
        return [el("span", { class: "mono" }, rule.key), (specs[rule.key] || {}).desc || "",
                num(rule.limit), num(rule.actual), el("span", { class: cls }, text)];
      }), [2, 3]));
  }
  if (board["judges/status"]) {
    kids.push(el("h3", null, "评审（" + board["judges/status"] + "）"),
      table(["评审", "结论", delivered ? "意见" : ""], JUDGES.map(([lens, name]) => {
        const verdict = board["judge." + lens + "/verdict"];
        if (!verdict) return [name, el("span", { class: "na" }, "未询问"), ""];
        const target = verdict.target === "thesis" ? "（论点）" : verdict.target === "levels" ? "（价位与仓位）" : "";
        return [name, el("span", { class: verdict.accept ? "pass" : "fail" }, (verdict.accept ? "接受" : "反对") + target),
                delivered ? verdict.critique || "" : ""];
      })));
    if (outcome && !delivered) kids.push(el("p", { class: "note" }, "没有交付结论，评审意见的正文不在这里展示；点过程记录里评审的卡片可以看到。"));
  }
  if (kids.length === 1) kids.push(el("div", { class: "muted" }, "还没有证据。"));
  fill(box, kids);
}

registerView({ id: "evidence", title: "证据", render: renderEvidence });
