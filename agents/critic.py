"""Critic：独立审计。三层判定，全部是确定性代码，不用 LLM。

为什么审计不用 LLM（这是本设计最重要的一句话）：
    如果审计者的判据本身也是"模型觉得对不对"，那它只会附和——
    同一个模型、同一份数据、同一个上下文，凭什么得出相反结论？
    所以审计的**判据必须确定**：数值比对用容差，口径合规用集合比较。
    LLM 只负责"表达"，不负责"判定"。

三层：
    L1 上下文隔离 —— 本方法只接收 artifact（代码 + 数值），拿不到 Analyst 的推理链；
    L2 实现路径隔离 —— 用 SQL 重算一遍（Analyst 用的是 Python/csv），比对数值；
    L3 口径知识隔离 —— 从指标字典取口径要求，检查 filters 是否满足。
"""
from __future__ import annotations

from agents.base import Agent
from core.blackboard import Critique
from core.messages import Intent
from tools.data_tools import l2_value

TOL = 0.01   # 金额容忍 1 分钱：太松漏检，太紧误驳


class Critic(Agent):
    name = "Critic"
    role = "独立审计"

    def __init__(self, llm, bus, bb, metric_dict):
        super().__init__(llm, bus, bb)
        self.md = metric_dict

    def run(self, artifact) -> Critique:
        issues = []
        got = artifact.value if artifact is not None else None

        # ---------- L2：换一条实现路径重算 ----------
        metric = self.bb.target_metric or "营收"
        expected = None
        l2_error = ""
        try:
            expected = l2_value(self.bb.filters, metric)
        except Exception as e:                                   # noqa: BLE001
            l2_error = repr(e)

        if l2_error:
            issues.append({"layer": "L2", "issue": "复算执行失败", "detail": l2_error})
        elif expected is None:
            # 两种可能：该指标没有第二实现路径（如毛利率），或分母为 0 无法计算。
            # 两种情况都不能判 PASS——没有独立复算就等于没审计。
            issues.append({"layer": "L2", "issue": f"「{metric}」没有可用的独立复算结果",
                           "detail": "该指标未实现第二路径，或筛选结果为空导致无法计算"})
            self.bus.event("tool", summary=f"L2 无法复算「{metric}」→ 无法验证")
        else:
            same = got is not None and abs(float(got) - expected) <= TOL
            got_txt = "None" if got is None else f"{float(got):,.2f}"
            self.bus.event("tool", summary=f"L2 独立复算（SQL 路径）：{expected:,.2f} "
                                          f"vs 执行结果 {got_txt} → {'一致' if same else '不一致'}")
            if not same:
                issues.append({"layer": "L2", "issue": "两条实现路径结果不一致",
                               "detail": f"SQL 复算 {expected:,.2f}，沙箱执行 {got_txt}；"
                                         f"请检查 filters 的扣减开关是否与实际计算一致"})

        # ---------- L3：口径合规性 ----------
        entry = self.md.get(self.bb.target_metric)
        if entry is None and self.bb.target_metric:
            # 真模型可能把"营收"写成"营业收入"，用字典别名归一，避免误判 UNVERIFIED
            hits = self.md.match(self.bb.target_metric)
            entry = hits[0] if hits else None
        if entry is None:
            entry = next(iter(self.md.match(self.bb.question)), None)
        if entry is not None:
            for miss in self.md.required_issues(entry, self.bb.filters):
                # 带上 field/expected：审计报告要能直接指导"改哪个开关"，
                # 否则 Planner 看到一段中文描述，只能猜自己哪一项没设对。
                issues.append({"layer": "L3", "issue": f"口径不合规：{miss['desc']}",
                               "detail": miss["source"],
                               "field": miss["key"], "expected": True})
            self.bus.event("tool", summary=f"L3 口径校验（字典条目：{entry['name']}，"
                                          f"版本 {self.md.doc.get('version')}）："
                                          f"{len(entry.get('required_filters', [])) - len(self.md.required_issues(entry, self.bb.filters))}"
                                          f"/{len(entry.get('required_filters', []))} 项满足")

        # ---------- 判定 ----------
        if any(i["layer"] == "L2" and i["issue"] == "两条实现路径结果不一致" for i in issues):
            verdict = "REVISE"
        elif expected is None:
            # 没有第二路径（含分母为 0）：如实说"无法验证"，不假装通过
            verdict = "UNVERIFIED"
        elif entry is None:
            verdict = "UNVERIFIED"
            issues.append({"layer": "L3", "issue": "指标字典未覆盖该指标，结果未经验证"})
        elif issues:
            verdict = "REVISE"
        else:
            verdict = "PASS"

        c = Critique(verdict=verdict, issues=issues,
                     evidence={"sql_value": expected, "exec_value": got,
                               "metric": entry["name"] if entry else None})

        if verdict == "REVISE":
            self.log("Planner", Intent.REVISE,
                     f"驳回 {artifact.aid if artifact else '-'}：{c.layer} / {issues[0]['issue']}",
                     payload={"issues": issues}, refs=[artifact.aid] if artifact else [])
        else:
            self.log("Reporter", Intent.CRITIQUE, f"{verdict}（{c.layer}）",
                     payload={"issues": issues}, refs=[artifact.aid] if artifact else [])
        self.bus.event("verdict", summary=f"{verdict} | 层 {c.layer} | "
                                          + (issues[0]["issue"] if issues else "全部检查通过"),
                       issues=issues)
        return c
