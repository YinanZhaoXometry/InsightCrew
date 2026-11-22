/* 00-core.js —— 小工具：DOM 助手、格式化、引用徽章与视图注册表。
   本文件（以及一切编号 20 以下的源文件）在加载时不碰 document 或 window，
   只有被调用时才会，这样 Node 测试不用浏览器就能加载它们（BLUEPRINT 20.9）。 */

const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "on") for (const [name, fn] of Object.entries(value)) node.addEventListener(name, fn);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));       // 字符串成为文本节点，不会被当作标记
  }
  return node;
}

function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}

/* 原型的描边图标（WP56）：page.html 的 .svg-library 里有 28 枚 <symbol>，这里按 id 引用。
   只在浏览器的渲染路径里调用（Node 测试的假 DOM 没有 createElementNS）。 */
function icon(name, cls) {
  const node = svg("svg", { class: "icon" + (cls ? " " + cls : ""), "aria-hidden": "true" });
  node.append(svg("use", { href: "#i-" + name }));
  return node;
}

function fill(node, ...kids) {
  node.replaceChildren(...kids.flat(Infinity).filter((kid) => kid !== null && kid !== undefined && kid !== false));
}

function num(value, places = 4) {                 // 展示格式：至多 places 位小数，去掉多余的零
  if (value === null || value === undefined) return "—";
  const text = Number(value).toFixed(places).replace(/\.?0+$/, "");
  return text === "" || text === "-" || text === "-0" ? "0" : text;
}
const count = (value) => Number(value || 0).toLocaleString("en-US");
const reasonText = (reason) => REASON[reason] || reason || "";
const withUnit = (value, unit) => value === undefined || value === null ? "—"
  : num(value) + (unit === "%" ? "%" : unit ? " " + unit : "");

function table(header, rows, numeric = []) {
  const cell = (tag, value, i) => el(tag, { class: numeric.includes(i) ? "num" : null }, value);
  return el("table", null,
    el("thead", null, el("tr", null, header.map((h, i) => cell("th", h, i)))),
    el("tbody", null, rows.map((row) => el("tr", { class: row.cls || null }, row.map((v, i) => cell("td", v, i))))));
}

/* 视图注册表：每个视图 {id, title, render(model, element)}，可选 always（总在渲染的部分）。
   外壳每帧至多调一次 render，只画可见页签与总在渲染的部分（BLUEPRINT 20.9）。 */
const VIEWS = [];
const registerView = (view) => VIEWS.push(view);

/* 引用徽章：角色在文本里引用的事实或条目编号，点开它指名的内容（WP48 会换成完整的证据面板） */
function cite(id) {
  return el("button", { class: "chip mono", type: "button", title: "引用：" + id,
                        on: { click: () => openCite(id) } }, id);
}

/* 引用面板的挂点（HANDOFF 第 7 节「Version 2.3: shared-file requests」WP48 那行，WP51 落实）：
   renderCite（60-events.js）画完面板后调用它；40-evidence.js 在加载时把它设成「在证据中查看」的补充。
   绑定声明在本文件（编号最小、最先加载），40-evidence 加载时赋值才不踩 60-events 的暂时性死区。 */
let citePanelExtra = null;
