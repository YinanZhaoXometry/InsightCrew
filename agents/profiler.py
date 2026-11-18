"""Profiler：把表结构翻译成人能看懂的含义与风险。

为什么需要它（而不是让 Planner 自己看 schema）：
    Planner 拿到裸 schema 时，注意力会落在"怎么算"上；
    有了画像卡，它拿到的已经是"哪里会踩坑"的结论。
    这一步的产出质量，直接决定后面口径错的发生率。

答辩追问时的答法：去掉 Profiler 的消融实验里，口径错误率会上升。
"""
from __future__ import annotations

from agents.base import Agent
from core.messages import Intent
from tools.data_tools import schema_text

SYSTEM = """你是数据分析团队中的 Profiler（数据画像专家）。
你只做一件事：把表结构翻译成字段含义与风险清单，不做任何计算，不给分析方案。

输出 JSON：
{"columns":[{"name":"字段名","type":"类型","meaning":"业务含义","risk":"潜在坑点"}],
 "risks":["整体层面的数据风险"],
 "notes":"一句话说明数据规模与时间覆盖"}

硬性约束：
1) 不得臆测数据中不存在的字段；
2) risk 必须具体（"跨季度边界"优于"注意日期"）；
3) 不给分析建议，那是 Planner 的职责。"""


class Profiler(Agent):
    name = "Profiler"
    role = "数据画像"

    def run(self) -> dict:
        schema = schema_text()
        out = self.llm.chat_json("profiler", SYSTEM,
                                 f"数据表结构：\n{schema}\n\n用户问题：{self.bb.question}")
        self.bb.profile = out
        n_col = len(out.get("columns", []))
        n_risk = len(out.get("risks", []))
        self.log("Planner", Intent.PROFILE, f"画像卡：{n_col} 个字段 / {n_risk} 条风险")
        self.bus.event("state", summary=f"数据画像完成，识别 {n_risk} 条风险")
        return out
