"""Planner：拆步骤 + 把口径写成结构化 filters。

这是整个系统最关键的一个设计：
    **口径必须是结构化字段，不能是散文。**
    如果计划里只写"按财务口径计算营收"，审计者就无法判断合规与否，
    只能再问一遍模型"你觉得对吗"——那等于没有审计。

    filters 里的布尔开关（exclude_cancelled / coupon_deduction / subtract_refund）
    让 L3 审计变成一次集合比较：确定性、可测试、可复现。
"""
from __future__ import annotations

import json

from agents.base import Agent
from core.messages import Intent
from core.question_parser import REGIONS, coverage

SYSTEM = """你是数据分析团队中的 Planner（分析规划者）。
职责：把自然语言问题拆成可执行的步骤，并显式写出"口径参数"。

输出 JSON：
{"target_metric":"指标名，必须严格取自：营收 / 订单量 / 客单价 / 退款率",
 "steps":[{"step_id":1,"desc":"可执行的一步","expected":"这一步的产出"}],
 "filters":{"region":"地区","start":"YYYY-MM-DD","end":"YYYY-MM-DD",
            "exclude_cancelled":true,"coupon_deduction":true,"subtract_refund":true}}

硬性约束：
1) filters 六个键必须全部出现，布尔值只写 true/false，不许写 "maybe"；
2) 若上一轮被审计驳回，必须针对每一条 issue 修改 filters，不得原样重交；
   issue 里若带 "field" 与 "expected"，说明那一项口径开关必须被设为该值，逐条照做；
3) 步骤不超过 5 步，每步都要能被一段代码直接执行；
4) region 只能取给定地区列表中的值；**若问题是"全公司/全国/所有地区/不限定地区"，region 必须填空字符串 ""**；
5) 时间必须落在数据覆盖区间内；不要为了"看起来合理"而自己编一个地区；
6) 只做四则运算能算出来的指标，不要发明新指标名。"""


class Planner(Agent):
    name = "Planner"
    role = "分析规划"

    def run(self, critique=None) -> dict:
        lo, hi = coverage()
        parts = [f"用户问题：{self.bb.question}",
                 f"数据覆盖区间：{lo} ~ {hi}（规划必须落在此区间内，否则无数据可算）",
                 f"地区字段取值只能是：{'、'.join(REGIONS)}",
                 f"数据画像卡：{json.dumps(self.bb.profile, ensure_ascii=False)}"]
        if critique is not None and critique.issues:
            parts.append("上一轮被审计驳回，问题清单：\n"
                         + json.dumps(critique.issues, ensure_ascii=False, indent=2)
                         + "\n请逐条修正 filters 后重新提交计划。")
        out = self.llm.chat_json("planner", SYSTEM, "\n\n".join(parts))

        ver = self.bb.new_plan(out.get("steps", []), out.get("filters", {}),
                               out.get("target_metric", ""))
        note = f"v{ver} 计划：{len(self.bb.plan)} 步，指标={self.bb.target_metric}"
        if critique is not None:
            note = "根据驳回意见重规划 → " + note
        self.log("Analyst", Intent.PLAN, note,
                 payload={"filters": self.bb.filters, "steps": out.get("steps", [])})
        return out
