"""指标计算注册表：审计者（L2）的 SQL 实现路径。

设计要点（答辩时这条最容易被追问）：
    Analyst 用 Python/csv 算，Critic 用 SQL 算，两条路径**独立实现同一份口径**。
    所以这里的每个指标都必须与 data/metric_dict.json 的公式逐字对应，
    并且 supported_metrics() 会做一次一致性检查——字典里有、这里没有，
    或这里有、字典里没有，都会直接报错。

    这就是"口径漂移"的防线：改字典不改实现（或反之）会立刻暴露，
    而不是等到审计给出一个莫名其妙的 PASS。

    注意：本文件只被 Critic 使用。Analyst 拿不到它——
    否则"独立复算"就退化成"照着审计的写法再抄一遍"。
"""
from __future__ import annotations

import sqlite3

from tools.data_tools import sql

# 当前实现了双路径审计的指标。毛利率不在其中：数据集没有成本字段，
# 硬算出来一定是编的，所以系统会明确拒绝（见 question_parser.scope）。
SUPPORTED = ("营收", "订单量", "客单价", "退款率")


# --------------------------------------------------------------------- 基础
def _where(filters: dict) -> tuple[str, list]:
    """把结构化 filters 翻译成 WHERE 子句。所有指标共用同一套筛选语义。"""
    parts, params = [], []
    start, end = filters.get("start"), filters.get("end")
    if start and end:
        parts.append("o.order_date BETWEEN ? AND ?")
        params += [str(start), str(end)]

    region = filters.get("region")
    if region:
        regs = list(region) if isinstance(region, (list, tuple)) else [region]
        regs = [x for x in regs if x]
        if regs:
            parts.append("o.region IN (%s)" % ",".join("?" * len(regs)))
            params += regs

    if filters.get("exclude_cancelled"):
        parts.append("o.status <> 'cancelled'")

    return ("WHERE " + " AND ".join(parts)) if parts else "", params


def _num(x) -> float:
    return float(x or 0)


def _refund_sum(where: str, params: list) -> float:
    """退款金额。必须 JOIN 回订单表，才能复用同一套地区/时间/状态筛选。"""
    q = ("SELECT COALESCE(SUM(r.refund_amount), 0) FROM refunds r "
         f"JOIN orders o ON o.order_id = r.order_id {where}")
    return _num(sql(q, tuple(params))[0][0])


# --------------------------------------------------------------------- 四个指标
def revenue(filters: dict) -> float:
    """营收 = SUM(amount - coupon 若扣券) - SUM(refund 若扣退款)"""
    where, params = _where(filters)
    amt = "o.amount - o.coupon" if filters.get("coupon_deduction") else "o.amount"
    total = _num(sql(f"SELECT COALESCE(SUM({amt}), 0) FROM orders o {where}",
                     tuple(params))[0][0])
    if filters.get("subtract_refund"):
        total -= _refund_sum(where, params)
    return round(total, 2)


def order_count(filters: dict) -> float:
    """订单量 = COUNT(DISTINCT order_id)，口径同上（排除取消由 filters 决定）。"""
    where, params = _where(filters)
    return float(sql(f"SELECT COUNT(DISTINCT o.order_id) FROM orders o {where}",
                     tuple(params))[0][0])


def aov(filters: dict) -> float | None:
    """客单价 = 营收 ÷ 订单量。

    分子分母必须同口径：这里强制让分子按营收口径算（含券与退款的开关），
    分母按订单量口径算（排除取消）。分母为 0 时返回 None——
    宁可不给结论，也不要拿 0 去做除法再报一个无穷大。
    """
    n = order_count(filters)
    if n <= 0:
        return None
    return round(revenue(filters) / n, 2)


def refund_rate(filters: dict) -> float | None:
    """退款率 = 退款金额 ÷ 订单金额，以百分数表示（3.21 表示 3.21%）。

    分母用 amount（券前），与字典公式一致；分子必须真的关联 refunds 表。
    """
    where, params = _where(filters)
    gross = _num(sql(f"SELECT COALESCE(SUM(o.amount), 0) FROM orders o {where}",
                     tuple(params))[0][0])
    if gross <= 0:
        return None
    return round(_refund_sum(where, params) / gross * 100, 2)


_DISPATCH = {"营收": revenue, "订单量": order_count, "客单价": aov, "退款率": refund_rate}


def compute(metric: str, filters: dict) -> float | None:
    """按指标名派发。不支持的指标返回 None（由 Critic 判 UNVERIFIED）。"""
    fn = _DISPATCH.get(metric)
    if fn is None:
        return None
    return fn(filters)


# --------------------------------------------------------------------- 一致性
def consistency_report(metric_dict) -> tuple[list[str], list[str]]:
    """字典与实现的漂移检查。返回 (错误, 提示)。

    错误：实现里有、字典没有——口径无处可查，必须修。
    提示：字典里有、实现没有——属于"已知不支持"（如毛利率缺成本字段），
          系统会明确拒绝而不是硬算，因此只提示不报错。
    """
    errors, warnings = [], []
    dict_names = {e["name"] for e in metric_dict.entries}
    for name in SUPPORTED:
        if name not in dict_names:
            errors.append(f"实现里支持「{name}」，但指标字典没有该条目（口径无处可查）")
    for name in dict_names:
        if name not in SUPPORTED:
            warnings.append(f"字典里有「{name}」，L2 未实现（该指标会被拒绝作答，属预期行为）")
    return errors, warnings
