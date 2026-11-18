"""Analyst：写代码 + 在沙箱里执行 + 自修复。

自修复的边界很重要：
    语法错、字段名错 -> 自己改（同一层内重试）；
    口径错、逻辑错 -> 不自己改，等审计驳回后由 Planner 重新规划。
    这条边界就是"分层协作"和"一条流水线"的分水岭。
"""
from __future__ import annotations

from agents.base import Agent
from core import sandbox
from core.messages import Intent
from tools.data_tools import data_files, schema_text


def _strip_fence(code: str) -> str:
    """模型常把代码裹在 ```python 围栏里，直接执行会语法错。"""
    if not code.startswith("```"):
        return code
    lines = code.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

SYSTEM = """你是数据分析团队中的 Analyst（分析执行者）。
按给定计划写 Python 代码并真实执行，不得编造数字。

可用数据：当前工作目录下的 orders.csv、refunds.csv（只能用 csv / json / math / statistics 模块）。

硬性约束：
1) 禁止导入 os、sys、subprocess、socket 等系统模块，禁止写文件、联网；
2) 代码最后必须打印一行：RESULT:{"value": 数值, "unit": "单位", "method": "计算口径"}
3) value 必须是算出来的，不许写成常量；
4) **是否扣减优惠券 / 退款、是否排除取消单，严格以"口径参数"里的布尔值为准**，
   不得自行增加或省略——你多扣一次或少扣一次，都会与独立复算对不上；
5) 只输出 JSON：{"code": "完整可运行代码", "plan_note": "一句话说明"}"""


class Analyst(Agent):
    name = "Analyst"
    role = "分析执行"

    def __init__(self, llm, bus, bb, timeout: float = 30.0, metric_dict=None):
        super().__init__(llm, bus, bb)
        self.timeout = timeout
        self.md = metric_dict

    def run(self, max_repair: int = 1, hint: str = ""):
        prompt = (f"用户问题：{self.bb.question}\n"
                  f"分析计划：{self._plan_text()}\n"
                  f"口径参数：{self.bb.filters}\n"
                  f"目标指标：{self.bb.target_metric or '营收'}\n"
                  f"指标定义（仅供理解口径，是否扣减仍以上面的口径参数为准）：{self._definition()}\n"
                  f"表结构：\n{schema_text()}\n"
                  f"数据画像要点：{self.bb.profile.get('risks', [])}")
        if hint:
            # 审计只告诉它"不一致、偏差多少"，不给正确数值——否则就成了抄答案
            prompt += f"\n\n审计反馈（上一版代码已被驳回）：{hint}"
            max_repair = max(max_repair, 1)
        artifact = None

        for attempt in range(max_repair + 1):
            out = self.llm.chat_json("analyst", SYSTEM, prompt)
            code = _strip_fence((out.get("code") or "").strip())
            if not code:
                # 模型偶尔只给解释不给代码：当成一次失败，把话退回去让它重写
                self.bus.event("tool", summary="模型未返回代码，要求重写")
                prompt += "\n\n上一次没有返回 code 字段。请只输出 JSON，code 必须是完整可运行代码。"
                continue
            res = sandbox.run(code, data_files(), timeout=self.timeout)
            payload = res.get("result") or {}
            artifact = self.bb.add_artifact(
                step_id=self.bb.plan[-1].step_id if self.bb.plan else 1,
                code=code, run_result=res,
                value=payload.get("value"), unit=payload.get("unit", ""),
                method=payload.get("method", ""),
            )
            if res["ok"] and payload.get("value") is not None:
                self.bus.event("tool", summary=f"沙箱执行成功：{artifact.aid} value={artifact.value:,.2f} "
                                              f"({res['elapsed']}s) {artifact.method}")
                self.log("Critic", Intent.EXECUTE_STEP,
                         f"产出 {artifact.aid}：{artifact.value:,.2f} {artifact.unit}",
                         payload={"value": artifact.value, "method": artifact.method},
                         refs=[artifact.aid])
                return artifact

            # 失败：把结构化错误回传，让它自己修
            # 注意两种失败要分开说：报错（stderr 有内容）vs 跑完但没打印 RESULT。
            # 后者如果只回传空 stderr，模型完全不知道自己错在哪，会原样再提交一次。
            kind = (f"报错（{res['tb_type']}）" if res.get("tb_type")
                    else "执行完成但没有打印 RESULT 行（格式或字段名不对）")
            detail = (res.get("stderr") or "")[-600:] or (res.get("stdout") or "")[-600:] \
                or "（没有任何输出）"
            self.bus.event("tool", summary=f"沙箱执行失败：{kind}（第 {attempt + 1} 次）")
            prompt += (f"\n\n上一次执行{kind}：\n{detail}\n"
                       f"请修正后重新提交代码，务必最后打印一行 RESULT:{{...}}。")

        self.bus.event("tool", summary="自修复次数用尽，交由审计判定")
        return artifact

    def _plan_text(self) -> str:
        return "；".join(f"({s.step_id}) {s.desc}" for s in self.bb.plan) or "（无计划）"

    def _definition(self) -> str:
        """从口径字典取"定义"文本给执行者看。

        只给 definition，不给 required_filters——执行者该知道"这个指标是什么"，
        但不该知道"审计要检查哪几项"，否则它会照着判据写代码，审计就失去意义。
        """
        if self.md is None:
            return "（无）"
        entry = self.md.get(self.bb.target_metric)
        if entry is None:
            return "（无）"
        return f"{entry.get('definition', '')} 公式：{entry.get('formula', '')}"
