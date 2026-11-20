/* 页面 Node 测试的公共装载器：按页面同样的顺序加载编号 20 以下的源文件
   （它们加载时不碰 document/window），在同一个函数作用域里求值——
   和拼装后的页面同一套作用域规则（BLUEPRINT 20.9、20.12）。 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../insightcrew/app/assets/js");

export function loadBelow20() {
  const files = ["00-core.js", "05-labels.js", "10-model.js"];
  const source = files.map((name) => readFileSync(path.join(SRC, name), "utf8")).join("\n");
  const wrapper = `(function () {\n"use strict";\n${source}\nreturn { fold, STAGES, REASON, STATUS_TEXT, RULE_LABEL, CHECK_LABEL };\n})()`;
  return vm.runInNewContext(wrapper, {});
}

/** 手造一条事件，形状与 journal.jsonl 里的一致 */
export function ev(seq, type, actor, data, step = "") {
  return { seq, ts: "2026-11-20T00:00:00Z", type, actor, step, data, caused_by: [] };
}
