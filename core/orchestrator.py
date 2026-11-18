"""编排器：唯一的驱动循环。

它的职责只有四件：
    1) 按顺序驱动角色（顺序本身就是"协调策略"）；
    2) 把审计结论作为下一轮的输入（反馈边，这是多智能体的命脉）；
    3) 检查三重终止条件；
    4) 任何时候都能给出一份降级输出（演示可用性的底线）。

注意：这里没有用 LangGraph/CrewAI。原因是这个项目的评分点在"机制是否清晰"，
自己写 150 行反而更容易讲清楚，也更容易被追问细节时答得上来。
"""
from __future__ import annotations

import os
import re
import time

from agents.analyst import Analyst
from agents.critic import Critic
from agents.planner import Planner
from agents.profiler import Profiler
from agents.reporter import Reporter
from config import Config, RUNS_DIR
from core.blackboard import Blackboard
from core.llm import LLM, ScriptedLLM
from core.messages import MessageBus
from core.question_parser import REGIONS, available_regions, coverage, scope
from tools.metric_dict import MetricDict


class Orchestrator:
    def __init__(self, question: str, cfg: Config | None = None, verbose: bool = True):
        self.cfg = cfg or Config()
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(RUNS_DIR, self.run_id)

        self.bus = MessageBus(self.run_dir, verbose=verbose)
        self.bb = Blackboard(question=question)
        self.llm = LLM(self.cfg, scripted=ScriptedLLM() if self.cfg.offline else None)
        self.md = MetricDict()

        self.profiler = Profiler(self.llm, self.bus, self.bb)
        self.planner = Planner(self.llm, self.bus, self.bb)
        self.analyst = Analyst(self.llm, self.bus, self.bb, timeout=self.cfg.sandbox_timeout,
                               metric_dict=self.md)
        self.critic = Critic(self.llm, self.bus, self.bb, self.md)
        self.reporter = Reporter(self.llm, self.bus, self.bb, metric_dict=self.md)

        mode = "离线剧本" if self.cfg.offline else f"{self.cfg.model} @ {self.cfg.base_url}"
        self.bus.event("state", summary=f"启动（{mode}）｜问题：{question}")

    # ------------------------------------------------------------------ #
    def run(self) -> Blackboard:
        t0 = time.time()

        # 起跑前的范围检查：答不了的问题要当场说"答不了"。
        # 静默套用默认参数、给出一个看起来很确定的错数字，比拒绝作答危险得多。
        # 真模型也一样要过这一关：模型不认识"我们没有成本字段"这件事，
        # 放过它就会一本正经地算出一个毛利率。
        sc = scope(self.bb.question)
        if not sc["ok"]:
            # 把"系统理解成了什么"写进黑板，报告里能看出来它没懂错、只是算不了
            p = sc["parsed"]
            self.bb.filters = {"region": p["region"], "start": p["start"], "end": p["end"]}
            self.bb.target_metric = p["metric"]
            self.bus.event("scope", summary=f"超出能力范围：{sc['reason']}")
            self.bb.note(f"范围检查未通过：{sc['reason']}")
            return self._degrade(f"问题超出当前能力范围：{sc['reason']}（未给出数字）", t0)

        self.profiler.run()

        critique = None
        hint = ""
        history: list[str] = []

        while True:
            # ---- 终止条件 1：轮数预算 ----
            if self.bb.turn >= self.cfg.max_turns:
                return self._degrade("轮数预算耗尽", t0)
            # ---- 终止条件 2：时间预算 ----
            if time.time() - t0 > self.cfg.max_seconds:
                return self._degrade("时间预算耗尽", t0)

            self.bb.turn += 1
            self.bb.tokens = self.llm.tokens
            self.bus.event("state", summary=f"第 {self.bb.turn} 轮开始（累计 {self.llm.tokens} tokens）")

            # 真模型会超时、限流、返回垃圾；任何一种都不该让整个演示崩掉。
            # 统一收口成"降级输出"，这也是编排器该有的兜底责任。
            try:
                if hint:
                    # 计划与口径都合规、只有实现算错 → 计划不动，让执行者重算。
                    # 这一条是"分层纠错"的关键：错误要回到能修它的那一层。
                    self.bus.event("state", summary="同层修复：计划不变，执行者按偏差重算")
                    artifact = self.analyst.run(hint=hint)
                else:
                    self.planner.run(critique)      # 有驳回意见就带着改
                    bad = self._normalize_filters() or self._coverage_guard()
                    if bad:
                        return self._degrade(bad, t0)
                    artifact = self.analyst.run()
                critique = self.critic.run(artifact)
            except Exception as e:                  # noqa: BLE001
                return self._degrade(f"运行中断：{type(e).__name__}：{e}", t0)

            self.bb.critiques.append(critique)

            if critique.verdict == "PASS":
                self.reporter.run(verified=True)
                return self._finish(t0)
            if critique.verdict == "UNVERIFIED":
                self.reporter.run(verified=False)
                return self._finish(t0)

            # ---- 终止条件 3：停滞（同一问题反复驳回）----
            sig = critique.signature()
            history.append(sig)
            if history.count(sig) >= self.cfg.max_repeat_issue:
                return self._degrade("同一口径问题反复驳回，判定为停滞", t0)

            hint = self._next_hint(critique)

    # ------------------------------------------------------------------ #
    def _next_hint(self, critique) -> str:
        """决定"下一步该谁来修"。

        计划错（L3 口径不合规）-> 退回 Planner 重新规划（hint 留空）。
        计划对、实现错（只有 L2 数字不一致）-> 让 Analyst 带着偏差重算。

        这是"分层协作"最实在的一处：如果所有错误都退回 Planner，
        遇到纯实现 bug 就会原地打转——规划者改八次 filters 也修不好一个分母。
        """
        if any(i["layer"] == "L3" for i in critique.issues):
            return ""                                    # 口径问题，规划者的责任
        if not any(i["layer"] == "L2" for i in critique.issues):
            return ""
        ev = critique.evidence or {}
        exp, got = ev.get("sql_value"), ev.get("exec_value")
        try:
            dev = abs(float(got) - float(exp)) / abs(float(exp))
            detail = f"相对偏差约 {dev:.1%}。"
        except (TypeError, ValueError, ZeroDivisionError):
            detail = ""
        # 只给偏差、不给正确数值：告诉它有错，不告诉它答案。
        tip = ""
        entry = self.md.get(self.bb.target_metric)
        if entry:
            # 审计报告引用口径手册的公式与"常见错误"，是审计该做的事，
            # 不是泄题——执行者的 prompt 里本来就有定义，这里只是把话说透。
            tip = f"口径要求：{entry.get('formula', '')}；手册记录的高频错误：{entry.get('pitfall', '')}"
        return (f"独立复算与你的结果不一致。{detail}"
                f"计划与口径参数都没有变，问题在你的实现。请逐项核对口径参数："
                f"coupon_deduction=True 就必须减掉 coupon；subtract_refund=True 就必须关联 "
                f"refunds 表扣减退款；exclude_cancelled=True 就必须排除 cancelled。"
                f"{tip}"
                f"不要凭猜测调整数字。")

    # ------------------------------------------------------------------ #
    def _normalize_filters(self) -> str:
        """把模型写的地区值归一成数据里的真实取值。

        真模型很容易把"全公司"原样写进 region，于是 SQL 变成
        `region IN ('全公司')`，一行都匹配不到，返回 0——
        而 0 元和"没数据"长得一模一样，最容易被当成答案交付。
        所以这里把"全公司/全国/全部"映射成 None（= 不限地区），
        把"华东区"这种带后缀的写法削成"华东"，
        遇到字典里没有的地区则直接拒绝，而不是硬查一个空结果。
        """
        f = self.bb.filters
        # 问题本身就说了"全公司/全国"，那计划里写死某个大区一定是错的。
        # 这一条专门治一种很隐蔽的失败：模型看到地区列表，
        # 就给"全公司"硬挑了一个大区，数字看起来完全正常，其实口径已经错了。
        if re.search(r"全公司|全国|所有地区|全部地区|不分区|各大区|整体", self.bb.question or ""):
            f["region"] = None
            return ""

        r = f.get("region")
        if r is None or r == "":
            f["region"] = None
            return ""
        if isinstance(r, (list, tuple)):
            raw = [str(x) for x in r]
        else:
            raw = [p for p in str(r).replace("，", ",").replace("、", ",").split(",") if p.strip()]

        all_words = {"全部", "全公司", "全国", "所有", "整体", "全区域", "全部区域"}
        # 校验基准是**数据里真实出现过的取值**，不是中国大区的常识列表。
        # 用常识列表校验，"西南"会通过、查不到行、返回 0.00——一个假答案。
        have = set(available_regions()) or set(REGIONS)
        cleaned, unknown = [], []
        for x in raw:
            x = str(x).strip().removesuffix("大区").removesuffix("区")
            if not x:
                continue
            if x in all_words:
                f["region"] = None
                return ""
            (cleaned if x in have else unknown).append(x)

        if unknown:
            return (f"计划里的地区 {'、'.join(unknown)} 在数据中没有任何订单"
                    f"（数据中只有 {'、'.join(sorted(have))}，或「全公司」）")
        f["region"] = cleaned[0] if len(cleaned) == 1 else (cleaned or None)
        return ""

    # ------------------------------------------------------------------ #
    def _coverage_guard(self) -> str:
        """计划的时间区间落在数据覆盖之外时，拒绝计算。

        没有这道闸，真模型规划出 2025 年的区间后，SQL 和沙箱**都会算出 0**，
        L2 一致、L3 合规，系统会自信地交付"0.00 元"——
        这比报错难看得多。宁可降级，也不给假数字。
        """
        lo, hi = coverage()
        st, en = self.bb.filters.get("start"), self.bb.filters.get("end")
        if not (lo and hi and st and en):
            return ""
        if str(en) < lo or str(st) > hi:
            return (f"计划区间 {st} ~ {en} 与数据覆盖 {lo} ~ {hi} 无交集，"
                    f"拒绝输出 0 元这种假答案")

        # 部分重叠：问题问 Q2，但数据只有 6 月起。
        # 既不整段作废，也不能假装覆盖了整季——把区间裁到真实覆盖范围，并在报告里写明。
        # 否则报告会顶着"2026-04-01 ~ 2026-06-30"的抬头，交付一个只含 6 月的数字。
        if str(st) < lo:
            self.bb.note(f"问题起点 {st} 早于数据起始 {lo}，实际统计自 {lo} 起")
            cv = (f"问题起点 {st} 早于数据起始 {lo}，实际统计区间已裁剪为 {lo} ~ {en}")
            if cv not in self.bb.caveats:
                self.bb.caveats.append(cv)
            self.bb.filters["start"] = st = lo
        if str(en) > hi:
            self.bb.note(f"问题终点 {en} 晚于数据截止 {hi}，实际统计至 {hi} 止")
            cv = (f"问题终点 {en} 晚于数据截止 {hi}，实际统计区间已裁剪为 {st} ~ {hi}")
            if cv not in self.bb.caveats:
                self.bb.caveats.append(cv)
            self.bb.filters["end"] = en = hi

        # 最后一道、也是最通用的一道：这套筛选条件到底能不能捞到订单。
        # 时间越界、地区写错、两者叠加，最终都表现为"一条都查不到"。
        # 只要捞到 0 行，就不允许往下算——因为 SUM 空集 = 0，
        # 而 0 是个合法数字，L2 会认同、L3 也合规，假答案会被当成真答案交付。
        n = self._matched_orders()
        if n is not None and n == 0:
            return (f"筛选条件（{self._filter_desc()}）在数据中匹配到 0 条订单，"
                    f"无法计算。0 元与「没有数据」不是一回事，拒绝输出")
        return ""

    def _matched_orders(self) -> int | None:
        """这套 filters 能匹配多少条订单。查不了就返回 None（不阻断）。"""
        try:
            from tools.data_tools import sql
            st, en = self.bb.filters.get("start"), self.bb.filters.get("end")
            r = self.bb.filters.get("region")
            where, params = "order_date BETWEEN ? AND ?", [st, en]
            if isinstance(r, (list, tuple)) and r:
                where += f' AND region IN ({",".join("?" * len(r))})'
                params += list(r)
            elif isinstance(r, str) and r:
                where += " AND region = ?"
                params.append(r)
            return int(sql(f'SELECT COUNT(*) FROM orders WHERE {where}', tuple(params))[0][0])
        except Exception:                                # noqa: BLE001
            return None

    def _filter_desc(self) -> str:
        f = self.bb.filters
        r = f.get("region")
        r = "、".join(r) if isinstance(r, (list, tuple)) else (r or "全公司")
        return f"{r}，{f.get('start')} ~ {f.get('end')}"

    # ------------------------------------------------------------------ #
    def _degrade(self, reason: str, t0: float) -> Blackboard:
        self.bb.degraded = True
        self.bb.note(f"降级输出：{reason}")
        self.bus.event("budget", summary=f"触发降级：{reason}")
        self.reporter.run(verified=False, degraded_reason=reason)
        return self._finish(t0)

    def _finish(self, t0: float) -> Blackboard:
        elapsed = round(time.time() - t0, 2)
        self.bb.tokens = self.llm.tokens
        self.bb.note(f"总耗时 {elapsed}s，模型调用 {self.llm.calls} 次")
        self.bb.save(os.path.join(self.run_dir, "blackboard.json"))
        self.bus.event("state", summary=f"结束：{elapsed}s / {self.bb.turn} 轮 / "
                                       f"{len(self.bb.artifacts)} 次执行")
        self.bus.close()
        return self.bb
