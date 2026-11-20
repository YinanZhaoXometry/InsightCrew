/* 40-evidence.js —— 证据页签（WP48，BLUEPRINT 20.10「Evidence」）。
   一张统一的事实表（带搜索框）：账本 / 基本面 / 工具 / 宏观，附种类徽章与引用它的角色；
   条目列表沿用原有标题，保留「来源原文，未经核验」「来源原话，未经核验」字样；
   每条通道一行状态、数据卡、组合状态、用到的课程。
   引用徽章点开的抽屉由共享的 renderCite（60-events.js）绘制；这里给它补「在证据中查看」
   ——共享文件本包不改（HANDOFF 5.6 规则 3，请求已写入第 7 节），故以观察器在每次重绘后追加。
   本文件加载时不碰 document/window：纯推导（evReadings / evFactRows / evFactMatches /
   evActorName）可被 Node 测试直接加载。 */

/* ---------- 纯推导（Node 测试直接调） ---------- */

function evActorName(actor) {
  /* 引用者的中文名：先查泳道（risk.aggressive、judge.evidence 都有），再按前缀查组名 */
  const lane = LANES.find(([key]) => key === actor);
  if (lane) return lane[1];
  const base = (actor || "").split(".")[0];
  return GROUP_NAME[base] || actor || "系统";
}

function evReadings(board) {
  /* 每个事实被哪些分析师的 claim 解读过：技术面、基本面与新闻（宏观）三条分析 */
  const map = {};
  const add = (factId, reading) => {
    if (!factId) return;
    (map[factId] || (map[factId] = [])).push(reading || "");
  };
  for (const key of ["technical/analysis", "fundamentals/analysis", "news/analysis"])
    for (const claim of ((board[key] || {}).claims) || []) add(claim.fact, claim.reading);
  return map;
}

function evFactRows(model) {
  /* 统一事实表的行：事实本体＋种类徽章文案＋分析师的解读＋引用它的角色（去重、按首次出现序） */
  const readings = evReadings(model.board);
  const rows = [];
  for (const [id, fact] of Object.entries(model.facts)) {
    const citers = [];
    for (const entry of model.cited_by[id] || []) {
      const name = evActorName(entry.actor);
      if (!citers.includes(name)) citers.push(name);
    }
    rows.push({
      id, kind: fact.kind, kindText: FACT_KIND_TEXT[fact.kind] || fact.kind,
      label: fact.label || (fact.kind === "tool" ? "（工具按需计算）" : ""),
      value: fact.value, valueText: num(fact.value), unit: fact.unit || "", asOf: fact.as_of || "",
      readings: readings[id] || [], citers,
    });
  }
  return rows;
}

function evFactMatches(row, query) {
  /* 搜索：编号、含义、数值、单位、数据日、种类、解读、引用者，全部不区分大小写 */
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  const hay = [row.id, row.label, row.valueText, row.unit, row.asOf, row.kindText,
               row.readings.join("；"), row.citers.join("、")].join(" ").toLowerCase();
  return hay.includes(q);
}

/* ---------- 页内状态（搜索框与「在证据中查看」的落点） ---------- */

let evQuery = "";            // 搜索框里的字
let evWanted = "";           // 「在证据中查看」点名的事实或条目编号
let evScrolled = false;      // 落点只滚一次，之后的重绘保留高亮不再滚动
let evModelRef = null;       // 最近一帧的模型：给抽屉的补充按钮判断编号是否已知（任意页签都要有）
let evCiteHooked = false;    // 抽屉的观察器只挂一次
let evBuilt = false;         // 骨架只建一次：搜索框是持久节点，重绘不换，输入焦点不丢
let evInputEl = null, evCountEl = null;

function evRevealWanted(box) {
  /* 落到被点名的那一行：加高亮并滚到眼前；行不在（被搜索滤掉/还没上板）就只记着 */
  if (!evWanted) return;
  const node = box.querySelector('[data-cite="' + evWanted + '"]');
  if (!node) return;
  const row = node.closest("tr");
  if (row) row.classList.add("hl");
  if (!evScrolled) { node.scrollIntoView({ block: "center" }); evScrolled = true; }
}

/* ---------- 引用抽屉的补充：「在证据中查看」（20.10） ---------- */

registerView({ id: "evidence-model", title: "", always: true, target: "view-evidence",
               render: (model) => evKeepModel(model) });

function evKeepModel(model) {
  /* 每帧记下模型（只在可见页签渲染的视图拿不到别页签时的模型）；首次顺带挂上抽屉的观察器 */
  evModelRef = model;
  if (evCiteHooked) return;
  evCiteHooked = true;
  const box = $("detail");
  if (box) new MutationObserver(() => evDecorateCite(box)).observe(box, { childList: true });
}

function evDecorateCite(box) {
  /* renderCite 每次重绘抽屉都会换掉全部子节点：这里在其后追加跳转按钮（幂等） */
  const id = state.cited;
  if (!id || !evModelRef) return;
  if (!(evModelRef.facts[id] || evModelRef.items[id])) return;   // 未知引用：没有可去的行
  if (box.querySelector("#ev-cite-link")) return;
  box.append(el("div", { class: "cite-actions" },
    el("button", { class: "ctl", type: "button", id: "ev-cite-link",
                   on: { click: () => evViewInEvidence(id) } }, "在证据中查看")));
}

function evViewInEvidence(id) {
  evWanted = id;
  evScrolled = false;
  evQuery = "";                                             // 清掉搜索，落点行一定可见
  if (evInputEl) evInputEl.value = "";
  closeDetail();
  selectTab("evidence");
  requestRender();
}

/* ---------- 各节 ---------- */

function evChannelNotes(board, kids) {
  for (const [key, name] of [["fundamentals", "基本面"], ["disclosures", "公告"], ["news", "新闻"],
                             ["sentiment", "情感"], ["macro", "宏观"]]) {
    const status = board[key + "/status"];
    if (!status) continue;                       // 这次运行没开这条通道：一行都不加
    const text = (CHANNEL_STATE[key] || {})[status.state] || status.state;
    kids.push(el("p", { class: "note" },
      name + "通道：" + text + (status.state !== "available" && status.reason ? "（" + status.reason + "）" : "")));
  }
}

function evPortfolioNote(board, kids) {
  const pstate = board["portfolio/state"];
  if (!pstate) return;                           // 没给组合文件时没有这个键
  const text = PSTATE_TEXT[pstate.state] || pstate.state;
  const held = pstate.state === "held" ? "数量 " + num(pstate.quantity) + "（" + (pstate.currency || "—")
      + "），占权益 " + num(pstate.position_pct) + "%" : "";
  const flat = pstate.state === "flat" ? "现金占权益 " + num(pstate.cash_pct) + "%"
      + "（" + (pstate.currency || "—") + "）" : "";
  kids.push(el("p", { class: "note" }, "组合状态：" + text
    + (held || flat || (pstate.reason ? "（" + pstate.reason + "）" : "")) + (held && pstate.cash_pct !== null
      ? "；现金占权益 " + num(pstate.cash_pct) + "%" : "")));
}

function evDataCard(board) {
  /* 行情数据卡（ledger/card）：这次分析所用数据的画像，报告第 10 节同款 */
  const card = board["ledger/card"];
  if (!card) return null;
  const kids = ["数据卡：首根 K 线 " + card.first_bar + "，末根 " + card.last_bar + "，共 " + card.bars
    + " 根；末根距分析日 " + card.stale_days + " 个自然日；丢弃无收盘的行 " + card.dropped_rows + " 行"];
  if (card.gaps && card.gaps.length) kids.push("缺口：" + card.gaps.join("；"));
  if (card.unavailable && card.unavailable.length)
    kids.push("不可用的事实：" + card.unavailable.map((u) => u.id + "（" + u.reason + "）").join("；"));
  if (card.warnings && card.warnings.length) kids.push("警告：" + card.warnings.join("；"));
  return el("p", { class: "note" }, kids.join("。"));
}

function evFundamentalsCard(card) {
  const notes = ["来源 " + card.source + "，申报货币 " + card.currency + "，最近财年期末 "
    + card.fiscal_year_end + "，所引最晚申报 " + card.filed + "，" + card.rows + " 行"];
  if (card.unavailable && card.unavailable.length)
    notes.push("不可用：" + card.unavailable.map((u) => u.id + "（" + u.reason + "）").join("；"));
  if (card.warnings && card.warnings.length) notes.push("重述提醒：" + card.warnings.join("；"));
  return el("p", { class: "note" }, notes.join("。"));
}

function evWindowCard(card) {
  return el("p", { class: "note" }, "窗口 " + card.window_from + " 到 " + card.window_to
    + "，" + card.items + " 条；丢弃无日期 " + card.dropped + " 条，截断 " + card.truncated + " 条");
}

function evSignalsList(signals, idField) {
  return el("ul", { class: "signals" }, signals.map((signal) => el("li", null,
    cite(signal[idField]), " "
    + (DIRECTION_TEXT[signal.direction] || signal.direction) + "——" + signal.reading)));
}

function evStanceNote(what, analysis) {
  return el("p", { class: "note" }, "窗口内" + what + "合起来的基调："
    + (STANCE_TEXT[analysis.stance] || analysis.stance) + "（把握：" + analysis.confidence + "）");
}

/* ---------- 证据页签 ---------- */

function evFactTable(rows) {
  return table(["编号", "含义", "数值", "单位", "数据日", "种类", "分析师的解读", "引用它的角色"],
    rows.map((row) => [
      el("span", { "data-cite": row.id }, cite(row.id)),
      row.label || "—",
      row.valueText,
      row.unit || "—",
      row.asOf || "—",
      el("span", { class: "kbadge " + row.kind }, row.kindText),
      row.readings.length ? row.readings.join("；") : "—",
      row.citers.length ? row.citers.join("、") : "—",
    ]), [2]);
}

function evBuildSkeleton(box) {
  /* 骨架只建一次：搜索框是持久节点，每帧只重画它上下的动态容器，输入不打断 */
  const input = el("input", { id: "ev-search", type: "search", autocomplete: "off",
    placeholder: "搜索事实：编号、含义、数值、单位、数据日、解读、引用者",
    on: { input: (event) => {
      evQuery = event.target.value;
      evWanted = "";                                     // 新搜索换新落点
      requestRender();
    } } });
  input.value = evQuery;
  evInputEl = input;
  evCountEl = el("span", { class: "ev-count" }, "");
  const facts = el("div", { id: "ev-facts" });
  const rest = el("div", { id: "ev-rest" });
  fill(box,
    el("div", { id: "ev-head" }, el("h2", null, "证据")),
    el("h3", null, "事实（账本 · 基本面 · 工具 · 宏观）"),
    el("div", { class: "ev-search" }, el("label", { for: "ev-search" }, "搜索"), input, evCountEl),
    el("p", { class: "note" },
      "账本事实与宏观事实都由代码计算并经第二条路径复算；基本面事实取自申报文件并同样复算；",
      "带「工具」徽章的是模型按需请求的指标。编号是引用徽章，点开即见这个事实与谁引用了它。"),
    facts, rest);
  evBuilt = true;
  return { facts, rest };
}

function renderEvidence(model, box) {
  evModelRef = model;
  if (!model.events.length) {
    evBuilt = false;                                     // 下次有运行时重建骨架
    fill(box, el("h2", null, "证据"),
      el("div", { class: "muted" }, "还没有运行。从上方发起一次运行，或从「过去的运行」里打开一次。"));
    return;
  }
  if (!evBuilt) evBuildSkeleton(box);
  const board = model.board;

  const head = [];
  evChannelNotes(board, head);
  evPortfolioNote(board, head);
  const dataCard = evDataCard(board);
  if (dataCard) head.push(dataCard);
  fill($("ev-head"), el("h2", null, "证据"), ...head);

  /* 统一事实表：搜索滤过的行 */
  const rows = evFactRows(model);
  const shown = rows.filter((row) => evFactMatches(row, evQuery));
  if (evCountEl)
    evCountEl.textContent = evQuery.trim() ? "显示 " + shown.length + " / " + rows.length + " 项"
                                           : "共 " + rows.length + " 项";
  fill($("ev-facts"),
    rows.length ? el("div", { class: "ev-scroll" },
        shown.length ? evFactTable(shown) : el("p", { class: "note" }, "（没有匹配的事实。换一个词试试。）"))
      : el("p", { class: "note" }, "（还没有事实：账本阶段没有跑或被拒绝。）"));

  /* 条目列表：沿用原有标题（「来源原文，未经核验」「来源原话，未经核验」）与各自的卡 */
  const kids = [];
  const items = board["disclosures/items"];
  if (items) {
    kids.push(el("h3", null, "公告条目（来源原文，未经核验）"),
      table(["编号", "发布日", "类别", "标题", "来源编号"], items.map((item) =>
        [el("span", { "data-cite": item.id }, cite(item.id)), item.published, item.kind, item.title,
         el("span", { class: "mono" }, item.ref)])));
    const dAnalysis = board["disclosures/analysis"];
    if (dAnalysis) {
      if (dAnalysis.signals && dAnalysis.signals.length) kids.push(evSignalsList(dAnalysis.signals, "item"));
      kids.push(evStanceNote("公告", dAnalysis));
    }
    if (board["disclosures/card"]) kids.push(evWindowCard(board["disclosures/card"]));
  }
  const newsItems = board["news/items"];
  if (board["news/status"] && board["news/status"].state !== "unavailable") {
    kids.push(el("h3", null, "新闻标题（来源原文，未经核验）"));
    if (newsItems && newsItems.length) {
      kids.push(table(["编号", "发布日", "来源", "标题"], newsItems.map((item) =>
        [el("span", { "data-cite": item.id }, cite(item.id)), item.published, item.source, item.title])));
    } else kids.push(el("p", { class: "note" }, "（窗口内没有新闻。）"));
    const nAnalysis = board["news/analysis"];
    if (nAnalysis) {
      if (nAnalysis.signals && nAnalysis.signals.length) kids.push(evSignalsList(nAnalysis.signals, "item"));
      kids.push(evStanceNote("新闻", nAnalysis));
    }
    if (board["news/card"]) kids.push(evWindowCard(board["news/card"]));
  }
  if (board["macro/status"] && board["macro/status"].state !== "unavailable") {
    const markets = board["macro/markets"] || [];
    if (markets.length) {
      kids.push(el("h3", null, "预测市场（来源原话，未经核验）"),
        el("p", { class: "note" }, "问题与概率照录自来源，程序不做核对；概率为来源给的隐含概率（0 到 1）。"),
        table(["编号", "问题", "隐含概率", "成交量", "截止日"], markets.map((m) =>
          [el("span", { "data-cite": m.id }, cite(m.id)), m.question, num(m.probability),
           num(m.volume), m.end_date]), [2, 3]));
    }
  }
  if (board["sentiment/status"] && board["sentiment/status"].state !== "unavailable") {
    const sAnalysis = board["sentiment/analysis"];
    kids.push(el("h3", null, "社交情感"));
    if (sAnalysis) {
      kids.push(el("p", { class: "note" }, "情绪带：" + (BAND_TEXT[sAnalysis.band] || sAnalysis.band)
        + "，情绪分 " + num(sAnalysis.score) + "（0 最看空，10 最看多）；把握：" + sAnalysis.confidence));
      if (sAnalysis.signals && sAnalysis.signals.length) kids.push(evSignalsList(sAnalysis.signals, "post"));
    }
    const posts = board["sentiment/posts"];
    if (posts && posts.length) {
      kids.push(table(["编号", "发布日", "来源", "自带标签", "内容"], posts.map((post) =>
        [el("span", { "data-cite": post.id }, cite(post.id)), post.published, post.source,
         TAG_TEXT[post.tag] || post.tag, post.text])));
    } else kids.push(el("p", { class: "note" }, "（窗口内没有帖子。）"));
    if (board["sentiment/card"]) kids.push(evWindowCard(board["sentiment/card"]));
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
  fill($("ev-rest"), kids);
  evRevealWanted(box);
}

registerView({ id: "evidence", title: "证据", render: renderEvidence });
