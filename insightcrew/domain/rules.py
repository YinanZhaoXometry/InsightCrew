"""规则手册的评估。提案在交付之前必须逐条通过这里的成文风控规则。

一个规则只有在 data/rules.toml（声明）和本文件（同名评估函数）两处都存在才算存在，
tests/test_rules.py 负责核对两边一致。formula 是给交易员和报告看的文字，本模块绝不求值它。
本模块是纯函数：不读文件、不看板、不调模型、不做 I/O。所有与界限的比较都经由 _slack，
统一允许 1e-9 的浮点误差，恰好落在界限上的提案视为通过（蓝图 6.6）。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal

from .types import Action, Proposal, Rating, RuleResult, RuleSpec, Thesis

_SLACK = 1e-9

# 评估顺序，与蓝图 6.6 的表格一致；两条 intake 规则不在此列，由 intake 自己调用。
ORDER: tuple[str, ...] = (
    "action_matches_rating",
    "hold_is_flat",
    "sell_requires_position",
    "stop_required",
    "stop_side",
    "size_range",
    "max_risk_per_trade_pct",
    "max_position_pct",
    "min_reward_risk",
    "min_stop_distance_atr",
    "entry_near_market",
    "cash_sufficient",
)

# 这五个比率规则需要可用的入场价与止损价：stop_required 或 stop_side 失败时它们一律 n/a。
RATIO_RULES: tuple[str, ...] = (
    "max_risk_per_trade_pct",
    "max_position_pct",
    "min_reward_risk",
    "min_stop_distance_atr",
    "entry_near_market",
)

def _slack(a: float, b: float) -> int:
    """a 相对 b 的方向：-1 明显小于，1 明显大于；误差 1e-9 之内视为相等（返回 0）。"""
    if a < b - _SLACK:
        return -1
    if a > b + _SLACK:
        return 1
    return 0


def _fmt(x: float) -> str:
    """数字进中文消息之前去掉多余的尾零，例：20.0 变 20，0.83000000001 变 0.83。"""
    return f"{x:.6g}"


def _result(spec: RuleSpec, status: Literal["pass", "fail"], actual: float | None,
            message: str) -> RuleResult:
    return RuleResult(key=spec.key, status=status, limit=spec.limit, actual=actual, message=message)


def _na(spec: RuleSpec, message: str) -> RuleResult:
    return RuleResult(key=spec.key, status="n/a", limit=spec.limit, actual=None, message=message)


def _no_limit(spec: RuleSpec) -> RuleResult:
    return _na(spec, "规则未配置 limit，无法评估")


# ---- intake 规则：此时事实账本还没建好，intake 直接给出原始数据 ----


def data_fresh(last_bar: date, as_of: date, spec: RuleSpec) -> RuleResult:
    """最后一个已结算 K 线到分析日的自然日数不超过上限。intake 在读完行情后调用。"""
    if spec.limit is None:
        return _no_limit(spec)
    days = float((as_of - last_bar).days)
    if _slack(days, spec.limit) <= 0:
        return _result(spec, "pass", days,
                       f"最后一根已结算 K 线距分析日 {_fmt(days)} 天，未超过上限 {_fmt(spec.limit)} 天")
    return _result(spec, "fail", days,
                   f"最后一根已结算 K 线距分析日 {_fmt(days)} 天，超过上限 {_fmt(spec.limit)} 天，数据过旧")


def min_history(bars: int, spec: RuleSpec) -> RuleResult:
    """已结算 K 线的根数不少于下限。intake 在读完行情后调用。"""
    if spec.limit is None:
        return _no_limit(spec)
    n = float(bars)
    if _slack(n, spec.limit) >= 0:
        return _result(spec, "pass", n, f"已结算 K 线 {_fmt(n)} 根，达到下限 {_fmt(spec.limit)} 根")
    return _result(spec, "fail", n,
                   f"已结算 K 线 {_fmt(n)} 根，不足下限 {_fmt(spec.limit)} 根，历史太短")


# ---- 提案规则：对除 intake 外的每条规则给出一个结果，顺序见 ORDER ----


def action_matches_rating(proposal: Proposal, thesis: Thesis, spec: RuleSpec) -> RuleResult:
    """交易动作必须与评级方向一致。适用于每一个提案。"""
    allowed = {
        Action.BUY: (Rating.BUY, Rating.OVERWEIGHT),
        Action.SELL: (Rating.UNDERWEIGHT, Rating.SELL),
        Action.HOLD: tuple(Rating),
    }[proposal.action]
    if thesis.rating in allowed:
        return _result(spec, "pass", None,
                       f"交易动作 {proposal.action.value} 与评级 {thesis.rating.value} 相符")
    return _result(spec, "fail", None,
                   f"交易动作 {proposal.action.value} 与评级 {thesis.rating.value} 不符："
                   "Buy 只能配 Buy 或 Overweight，Sell 只能配 Underweight 或 Sell")


def hold_is_flat(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """Hold 必须空仓：三个价位都是 null 且 size_pct 为 0。只适用于 Hold。"""
    if proposal.action is not Action.HOLD:
        return _na(spec, "规则只适用于 Hold 提案，本提案不适用")
    if any(v is not None for v in (proposal.entry, proposal.stop, proposal.target)):
        return _result(spec, "fail", proposal.size_pct,
                       "Hold 提案不应给出 entry、stop、target 任何一个价位")
    if _slack(proposal.size_pct, 0.0) != 0:
        return _result(spec, "fail", proposal.size_pct,
                       f"Hold 提案的仓位 {_fmt(proposal.size_pct)}% 不为 0")
    return _result(spec, "pass", proposal.size_pct, "Hold 提案未给出任何价位且仓位为 0，符合空仓要求")


def sell_requires_position(proposal: Proposal, spec: RuleSpec, position_pct: float) -> RuleResult:
    """Sell 只能减仓：必须有持仓、卖出不超过持仓、且不给出任何价位。只适用于 Sell。"""
    if proposal.action is not Action.SELL:
        return _na(spec, "规则只适用于 Sell 提案，本提案不适用")
    if _slack(position_pct, 0.0) <= 0:
        return _result(spec, "fail", position_pct,
                       f"当前没有持仓（{_fmt(position_pct)}%），Sell 提案无法减仓")
    if any(v is not None for v in (proposal.entry, proposal.stop, proposal.target)):
        return _result(spec, "fail", position_pct,
                       "Sell 提案不应给出 entry、stop、target 任何一个价位")
    if _slack(proposal.size_pct, position_pct) > 0:
        return _result(spec, "fail", position_pct,
                       f"卖出 {_fmt(proposal.size_pct)}% 超过当前持仓 {_fmt(position_pct)}%")
    return _result(spec, "pass", position_pct,
                   f"卖出 {_fmt(proposal.size_pct)}% 不超过当前持仓 {_fmt(position_pct)}%，且未给出任何价位")


def stop_required(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """Buy 必须有大于 0 的入场价与止损价。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if (proposal.entry is not None and proposal.stop is not None
            and _slack(proposal.entry, 0.0) > 0 and _slack(proposal.stop, 0.0) > 0):
        return _result(spec, "pass", None,
                       f"入场价 {_fmt(proposal.entry)} 与止损价 {_fmt(proposal.stop)} 都大于 0")
    return _result(spec, "fail", None, "Buy 提案缺少大于 0 的入场价或止损价")


def stop_side(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """止损必须低于入场价；给出目标价时目标必须高于入场价。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if proposal.entry is None or proposal.stop is None:
        return _na(spec, "入场价或止损价缺失，无法检查价位方向")
    if _slack(proposal.stop, proposal.entry) >= 0:
        return _result(spec, "fail", None,
                       f"止损 {_fmt(proposal.stop)} 不低于入场价 {_fmt(proposal.entry)}，方向错误")
    if proposal.target is not None and _slack(proposal.target, proposal.entry) <= 0:
        return _result(spec, "fail", None,
                       f"目标价 {_fmt(proposal.target)} 不高于入场价 {_fmt(proposal.entry)}，方向错误")
    return _result(spec, "pass", None,
                   f"止损 {_fmt(proposal.stop)} 低于入场价 {_fmt(proposal.entry)}，价位方向正确")


def size_range(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """Buy 的仓位必须落在 (0, 100] 区间内。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    size = proposal.size_pct
    if _slack(size, 0.0) <= 0:
        return _result(spec, "fail", size, f"仓位 {_fmt(size)}% 不大于 0，Buy 提案必须有正的仓位")
    if _slack(size, 100.0) > 0:
        return _result(spec, "fail", size, f"仓位 {_fmt(size)}% 超过 100%")
    return _result(spec, "pass", size, f"仓位 {_fmt(size)}% 在 (0, 100] 区间内")


def max_risk_per_trade_pct(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """单笔风险 = (entry − stop) / entry × size_pct，不超过上限。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if proposal.entry is None or proposal.stop is None:
        return _na(spec, "入场价或止损价缺失，无法计算单笔风险")
    if spec.limit is None:
        return _no_limit(spec)
    risk = (proposal.entry - proposal.stop) / proposal.entry * proposal.size_pct
    if _slack(risk, spec.limit) <= 0:
        return _result(spec, "pass", risk, f"单笔风险 {_fmt(risk)}%，未超过上限 {_fmt(spec.limit)}%")
    return _result(spec, "fail", risk, f"单笔风险 {_fmt(risk)}%，超过上限 {_fmt(spec.limit)}%")


def max_position_pct(proposal: Proposal, spec: RuleSpec, position_pct: float) -> RuleResult:
    """总仓位 = size_pct + position_pct，不超过上限。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if spec.limit is None:
        return _no_limit(spec)
    total = proposal.size_pct + position_pct
    note = f"（新仓 {_fmt(proposal.size_pct)}% 加现有持仓 {_fmt(position_pct)}%）"
    if _slack(total, spec.limit) <= 0:
        return _result(spec, "pass", total,
                       f"总仓位 {_fmt(total)}%{note}，未超过上限 {_fmt(spec.limit)}%")
    return _result(spec, "fail", total, f"总仓位 {_fmt(total)}%{note}，超过上限 {_fmt(spec.limit)}%")


def min_reward_risk(proposal: Proposal, spec: RuleSpec) -> RuleResult:
    """盈亏比 = (target − entry) / (entry − stop)，不低于下限；没有目标价时不适用。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if proposal.target is None:
        return _na(spec, "提案没有目标价，盈亏比不适用")
    if proposal.entry is None or proposal.stop is None:
        return _na(spec, "入场价或止损价缺失，无法计算盈亏比")
    if spec.limit is None:
        return _no_limit(spec)
    ratio = (proposal.target - proposal.entry) / (proposal.entry - proposal.stop)
    if _slack(ratio, spec.limit) >= 0:
        return _result(spec, "pass", ratio, f"盈亏比 {_fmt(ratio)}，不低于下限 {_fmt(spec.limit)}")
    return _result(spec, "fail", ratio, f"盈亏比 {_fmt(ratio)}，低于下限 {_fmt(spec.limit)}")


def min_stop_distance_atr(proposal: Proposal, spec: RuleSpec, facts: dict[str, float]) -> RuleResult:
    """止损距离 = (entry − stop) / atr_14，不低于下限。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if proposal.entry is None or proposal.stop is None:
        return _na(spec, "入场价或止损价缺失，无法计算止损距离")
    if "atr_14" not in facts:
        return _na(spec, "事实账本中没有 atr_14，无法计算止损距离")
    if spec.limit is None:
        return _no_limit(spec)
    distance = (proposal.entry - proposal.stop) / facts["atr_14"]
    if _slack(distance, spec.limit) >= 0:
        return _result(spec, "pass", distance,
                       f"止损距离 {_fmt(distance)} 倍 ATR，不低于下限 {_fmt(spec.limit)} 倍")
    return _result(spec, "fail", distance,
                   f"止损距离 {_fmt(distance)} 倍 ATR，低于下限 {_fmt(spec.limit)} 倍，止损贴着市价")


def entry_near_market(proposal: Proposal, spec: RuleSpec, facts: dict[str, float]) -> RuleResult:
    """入场价偏离 = abs(entry − close) / atr_14，不超过上限。只适用于 Buy。"""
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if proposal.entry is None:
        return _na(spec, "入场价缺失，无法计算与市价的距离")
    if "close" not in facts or "atr_14" not in facts:
        return _na(spec, "事实账本中没有 close 或 atr_14，无法计算入场价与市价的距离")
    if spec.limit is None:
        return _no_limit(spec)
    gap = abs(proposal.entry - facts["close"]) / facts["atr_14"]
    if _slack(gap, spec.limit) <= 0:
        return _result(spec, "pass", gap,
                       f"入场价偏离市价 {_fmt(gap)} 倍 ATR，未超过上限 {_fmt(spec.limit)} 倍")
    return _result(spec, "fail", gap,
                   f"入场价偏离市价 {_fmt(gap)} 倍 ATR，超过上限 {_fmt(spec.limit)} 倍，追价太远")


def cash_sufficient(proposal: Proposal, spec: RuleSpec, cash_pct: float | None) -> RuleResult:
    """仓位不超过可用现金占比；调用方没有给出现金占比时不适用。只适用于 Buy。

    这条规则的界限来自调用方的 cash_pct，所以结果里的 limit 记录的是它，而不是 TOML 的默认值。
    """
    if proposal.action is not Action.BUY:
        return _na(spec, "规则只适用于 Buy 提案，本提案不适用")
    if cash_pct is None:
        return _na(spec, "调用方未给出现金占比，无法检查")
    size = proposal.size_pct
    if _slack(size, cash_pct) <= 0:
        return RuleResult(key=spec.key, status="pass", limit=cash_pct, actual=size,
                          message=f"仓位 {_fmt(size)}% 不超过可用现金占比 {_fmt(cash_pct)}%")
    return RuleResult(key=spec.key, status="fail", limit=cash_pct, actual=size,
                      message=f"仓位 {_fmt(size)}% 超过可用现金占比 {_fmt(cash_pct)}%，现金不足")


def evaluate(proposal: Proposal, thesis: Thesis, facts: dict[str, float],
             specs: tuple[RuleSpec, ...], position_pct: float = 0.0,
             cash_pct: float | None = None) -> list[RuleResult]:
    """对除两条 intake 规则外的每条规则给出一个 RuleResult，顺序与蓝图 6.6 的表格一致。

    facts 是事实 ID 到数值的映射，这里只读 close 与 atr_14。在组合盘工作完成之前，
    position_pct 是 0，cash_pct 是 None。规则不适用给 n/a，不算通过；stop_required 或
    stop_side 失败时，五个比率规则无法在畸形提案上计算，一律 n/a。
    """
    by_key = {s.key: s for s in specs}

    def guarded(key: str, make: Callable[[RuleSpec], RuleResult]) -> RuleResult:
        # 缺一条声明只废掉那一条规则：给 n/a 并说明原因，其余规则照常评估。
        spec = by_key.get(key)
        if spec is None:
            return RuleResult(key=key, status="n/a", limit=None, actual=None,
                              message=f"规则手册缺少 {key} 的声明，无法评估")
        return make(spec)

    required = guarded("stop_required", lambda s: stop_required(proposal, s))
    sided = guarded("stop_side", lambda s: stop_side(proposal, s))
    broken = required.status == "fail" or sided.status == "fail"

    def ratio(key: str, make: Callable[[RuleSpec], RuleResult]) -> RuleResult:
        # 止损规则失败说明提案畸形，比率在这上面没有意义（entry − stop 甚至可能是 0），
        # 所以根本不去计算，直接给 n/a。这就是蓝图 6.6 说的级联。
        if broken:
            spec = by_key.get(key)
            return RuleResult(key=key, status="n/a", limit=spec.limit if spec else None,
                              actual=None,
                              message="入场价或止损价缺失或方向有误，比率无法计算，规则不适用")
        return guarded(key, make)

    return [
        guarded("action_matches_rating", lambda s: action_matches_rating(proposal, thesis, s)),
        guarded("hold_is_flat", lambda s: hold_is_flat(proposal, s)),
        guarded("sell_requires_position", lambda s: sell_requires_position(proposal, s, position_pct)),
        required,
        sided,
        guarded("size_range", lambda s: size_range(proposal, s)),
        ratio("max_risk_per_trade_pct", lambda s: max_risk_per_trade_pct(proposal, s)),
        ratio("max_position_pct", lambda s: max_position_pct(proposal, s, position_pct)),
        ratio("min_reward_risk", lambda s: min_reward_risk(proposal, s)),
        ratio("min_stop_distance_atr", lambda s: min_stop_distance_atr(proposal, s, facts)),
        ratio("entry_near_market", lambda s: entry_near_market(proposal, s, facts)),
        guarded("cash_sufficient", lambda s: cash_sufficient(proposal, s, cash_pct)),
    ]
