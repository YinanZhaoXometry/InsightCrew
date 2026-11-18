"""Reporter：把结论、口径、验证链、置信度写成报告。

设计原则：Reporter 只做"表达"，不做"计算"。
    报告里的数字只能来自黑板上的 artifact，不允许自己重算——
    否则等于又引入一条未经审计的数字来源，前面所有审计工作白费。
    答辩时可以这样讲：我们在职责上禁止了最后一个 agent 改写数字。
"""
from __future__ import annotations

from agents.base import Agent
from core.messages import Intent


class Reporter(Agent):
    name = "Reporter"
    role = "报告撰写"

    def __init__(self, llm, bus, bb, metric_dict=None):
        super().__init__(llm, bus, bb)
        self.md = metric_dict

    def run(self, verified: bool = True, degraded_reason: str = "") -> str:
        bb = self.bb
        v = bb.latest_value
        unit = next((a.unit for a in reversed(bb.artifacts) if a.value is not None), "")
        method = next((a.method for a in reversed(bb.artifacts) if a.value is not None), "")
        # 单位以口径字典为准，避免模型写"元"、字典写"CNY/笔"这种不一致
        if self.md is not None:
            entry = self.md.get(bb.target_metric)
            if entry and entry.get("unit"):
                unit = entry["unit"]

        f = bb.filters
        period = f"{f.get('start', '?')} ~ {f.get('end', '?')}"
        where = f.get("region") or "全部区域"
        if isinstance(where, (list, tuple)):
            where = "、".join(str(x) for x in where)
        flags = []
        if f.get("exclude_cancelled"):
            flags.append("已排除取消订单")
        if f.get("coupon_deduction"):
            flags.append("已扣减优惠券")
        if f.get("subtract_refund"):
            flags.append("已扣减退款")

        chain = []
        for i, c in enumerate(bb.critiques, 1):
            state = {"PASS": "通过", "REVISE": "驳回", "UNVERIFIED": "未验证"}.get(c.verdict, c.verdict)
            detail = "；".join(x["issue"] for x in c.issues) or "L2 复算一致，L3 口径合规"
            chain.append(f"{i}. 第 {i} 轮：**{state}**（{c.layer}）— {detail}")

        if not verified:
            conf, why = "低", "结论未通过独立审计，仅供参考"
        elif bb.degraded:
            conf, why = "低", f"预算/停滞降级输出（{degraded_reason}）"
        else:
            conf, why = "高", "L2 双路径复算一致，且满足指标字典全部口径要求"

        lines = [
            f"# 分析报告：{bb.question}",
            "",
            "## 结论",
            f"**{bb.target_metric or '目标指标'} = {v:,.2f} {unit}**" if v is not None
            else f"**{bb.target_metric or '目标指标'}：未能给出可信结论**",
            "",
            f"- 统计范围：{where}，{period}",
            f"- 口径处理：{'、'.join(flags) if flags else '（未做特殊处理）'}",
            f"- 计算路径：`{method or '未知'}`",
            "",
            "## 验证记录",
            *chain,
            "",
            "## 置信度",
            f"**{conf}** — {why}",
            "",
            "## 局限与未解决项",
        ]
        if bb.degraded:
            lines.append(f"- 本次为降级输出：{degraded_reason}")
        for cv in getattr(bb, "caveats", []):
            if cv not in lines:
                lines.append(f"- 数据覆盖说明：{cv}")
        if bb.critiques and bb.critiques[-1].issues:
            for it in bb.critiques[-1].issues:
                lines.append(f"- 未解决：{it['issue']}（{it.get('detail', '')}）")
        if not bb.degraded and (not bb.critiques or not bb.critiques[-1].issues):
            lines.append("- 未覆盖的口径（如税、汇率、跨期分摊）未纳入本次计算")
            lines.append(f"- 结论仅适用于 {where} / {period}，不宜外推至其他范围")

        bb.report = "\n".join(lines)
        self.log("User", Intent.FINALIZE, f"报告已生成（置信度 {conf}）")
        return bb.report
