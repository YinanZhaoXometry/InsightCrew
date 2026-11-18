"""注入评测（蓝图 12.3 的 `inject`）：审计必须拒绝每一个注入的故障，也不得误报有效变体。

案例在代码里手工构造：事实来自真实的 ACME 账本（data/market/synthetic，2026-09-30），
claim 的数值照抄账本，Buy 提案的入场、止损、目标、仓位全部由账本的 close 与 atr_14 推出，
让每一条规则都留有余量地通过。然后对蓝图 12.3 的清单逐项注入故障——契约是冻结的，
每次注入用 model_copy(update={...})，一个故障只动一处——再给出恰好压在界限上的有效变体。
"发现"的判据：审计给出的 Finding 里至少有一条 check 等于该行期望的检查项；有效变体必须
一条发现都没有。有漏报或误报就退出码 1。本模块不需要模型、不需要看板、不做 I/O 之外的事，
只是对纯领域函数（rules.evaluate 与 audit）的一次演示与体检。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from ..adapters.declarations import load_indicators, load_rules
from ..adapters.market_csv import CsvMarket
from ..domain import rules
from ..domain.audit import audit
from ..domain.ledger import build_ledger
from ..domain.types import Action, Analysis, Claim, Fact, Finding, Point, Proposal, Rating, RuleSpec, Thesis

_ROOT = Path(__file__).resolve().parent.parent.parent
_TICKER = "ACME"
_AS_OF = datetime.date(2026, 9, 30)

# 基线提案的形状：全部由账本的 close 与 atr_14 推出，留有余量。
_STOP_ATR = 1.5          # 止损在入场价下方 1.5 倍 ATR（界限是 1.0）
_TARGET_ATR = 2.5        # 目标价在入场价上方 2.5 倍 ATR，盈亏比 2.5/1.5（界限是 1.5）
_SIZE_PCT = 10.0         # 单笔风险约 0.30%（上限 1.0），总仓位 10%（上限 20）
_HORIZON_DAYS = 10       # 论点与提案一致的持有期，在 1 到 60 之间


@dataclass(frozen=True)
class Case:
    """一个待审案例：手工写好的分析、论点与提案，数字全部来自真实账本。"""

    analysis: Analysis
    thesis: Thesis
    proposal: Proposal


class Mutation(NamedTuple):
    """12.3 清单的一行：故障的说明、期望触发的检查项、以及注入这个故障的函数。"""

    label: str
    check: str
    apply: Callable[[Case], Case]


def _build_case(values: dict[str, float]) -> Case:
    """构造能通过全部规则的案例：三个 claim 照抄账本，论点引用被声称的事实，提案按 ATR 划价位。"""
    claims = tuple(
        Claim(fact=fact_id, value=values[fact_id], reading=reading)
        for fact_id, reading in (
            ("close", "收盘价站在中期与长期均线之上，趋势向上"),
            ("atr_14", "波动温和，止损可以放在规则要求的距离之外"),
            ("rsi_14", "动能偏强，但还没有到过热的程度"),
        )
    )
    analysis = Analysis(
        claims=claims,
        narrative="价格趋势向上，波动可控；短期延续的概率更大，适合按规则建立小仓位。",
    )
    thesis = Thesis(
        rating=Rating.BUY,
        horizon_days=_HORIZON_DAYS,
        points=(
            Point(text="价格站稳均线之上且动能偏强，趋势短期延续的可能性更大", cites=("close", "rsi_14")),
            Point(text="波动温和，风险能够控制在规则界限之内", cites=("atr_14",)),
        ),
        invalidation=("收盘价跌破止损位", "动能迅速转弱"),
        summary="趋势延续的看多论点，持有期与提案一致。",
    )
    entry = values["close"]
    stop = entry - _STOP_ATR * values["atr_14"]
    target = entry + _TARGET_ATR * values["atr_14"]
    proposal = Proposal(
        action=Action.BUY, entry=entry, stop=stop, target=target, size_pct=_SIZE_PCT,
        horizon_days=_HORIZON_DAYS, cites=("close", "atr_14"),
        rationale="趋势向上，入场在市价，止损与目标都按 ATR 划定，仓位留有余量。",
    )
    return Case(analysis=analysis, thesis=thesis, proposal=proposal)


def _evaluate(case: Case, facts: list[Fact], values: dict[str, float],
              specs: tuple[RuleSpec, ...]) -> list[Finding]:
    """规则手册加独立审计：这个案例在当前形态下的全部发现。"""
    rule_results = rules.evaluate(case.proposal, case.thesis, values, specs)
    return audit(facts, case.analysis, case.thesis, case.proposal, rule_results)


# ---- 注入用的少量帮助函数：契约是冻结的，改动只能通过 model_copy ----


def _with_claim(case: Case, index: int, **update: object) -> Case:
    """替换第 index 个 claim 的某个字段，其余 claim 原样保留。"""
    analysis = case.analysis
    claims = list(analysis.claims)
    claims[index] = claims[index].model_copy(update=update)
    return replace(case, analysis=analysis.model_copy(update={"claims": tuple(claims)}))


def _with_point_cites(case: Case, index: int, cites: tuple[str, ...]) -> Case:
    """把第 index 个论点引用的事实换成 cites。"""
    thesis = case.thesis
    points = list(thesis.points)
    points[index] = points[index].model_copy(update={"cites": cites})
    return replace(case, thesis=thesis.model_copy(update={"points": tuple(points)}))


# ---- 蓝图 12.3 的注入清单：18 行、19 个故障（持有期 0 与 500 是一行里的两个） ----


def _mutations(values: dict[str, float]) -> list[Mutation]:
    close, atr = values["close"], values["atr_14"]
    entry = close                                             # 基线提案的三个价位（_build_case 的取值）
    stop = entry - _STOP_ATR * atr
    target = entry + _TARGET_ATR * atr
    return [
        Mutation("claim 的数值放大 3%", "claim_value_matches_fact",
                 lambda c: _with_claim(c, 0, value=close * 1.03)),
        Mutation("claim 的数值换成另一事实的值", "claim_value_matches_fact",
                 lambda c: _with_claim(c, 0, value=values["sma_20"])),
        Mutation("claim 引用不存在的 rsi_15", "claim_cites_known_fact",
                 lambda c: _with_claim(c, 0, fact="rsi_15")),
        Mutation("论点引用没有任何 claim 声称的事实", "point_cites_claimed_fact",
                 lambda c: _with_point_cites(c, 0, ("sma_20",))),
        Mutation("论点持有期改为 0", "horizon_in_range",
                 lambda c: replace(c, thesis=c.thesis.model_copy(update={"horizon_days": 0}))),
        Mutation("论点持有期改为 500", "horizon_in_range",
                 lambda c: replace(c, thesis=c.thesis.model_copy(update={"horizon_days": 500}))),
        Mutation("止损抬到入场价之上", "stop_side",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"stop": entry + 0.5 * atr}))),
        Mutation("止损改为空", "stop_required",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"stop": None}))),
        Mutation("仓位放大到单笔风险越界", "max_risk_per_trade_pct",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"size_pct": 40.0}))),
        Mutation("仓位 25，单笔风险仍在界限内", "max_position_pct",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"size_pct": 25.0}))),
        Mutation("目标价压到盈亏比恰为 1.0", "min_reward_risk",
                 lambda c: replace(c, proposal=c.proposal.model_copy(
                     update={"target": entry + (entry - stop)}))),
        Mutation("止损只有 0.3 倍 ATR", "min_stop_distance_atr",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"stop": entry - 0.3 * atr}))),
        Mutation("入场价抬到 close + 5 倍 ATR", "entry_near_market",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={   # 止损与目标随之平移：
                     "entry": close + 5.0 * atr,                                # 否则 target 落到入场价之下，
                     "stop": close + 5.0 * atr - _STOP_ATR * atr,               # stop_side 先失败，五个比率
                     "target": close + 5.0 * atr + _TARGET_ATR * atr,           # 规则被级联成 n/a（见 12.3）。
                 }))),
        Mutation("Buy 配 Hold 评级", "action_matches_rating",
                 lambda c: replace(c, thesis=c.thesis.model_copy(update={"rating": Rating.HOLD}))),
        Mutation("Hold 提案带着 5% 仓位", "hold_is_flat",
                 lambda c: replace(c, proposal=c.proposal.model_copy(
                     update={"action": Action.HOLD, "entry": None, "stop": None, "target": None,
                             "size_pct": 5.0}))),
        Mutation("Sell 提案但当前没有持仓", "sell_requires_position",
                 lambda c: replace(c, proposal=c.proposal.model_copy(
                     update={"action": Action.SELL, "entry": None, "stop": None, "target": None}))),
        Mutation("提案引用不存在的事实", "proposal_cites_known_fact",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"cites": ("pe_ratio",)}))),
        Mutation("提案持有期与论点不一致", "horizon_matches_thesis",
                 lambda c: replace(c, proposal=c.proposal.model_copy(update={"horizon_days": 11}))),
        Mutation("三个价位同时除以 100", "entry_near_market",
                 lambda c: replace(c, proposal=c.proposal.model_copy(
                     update={"entry": entry / 100, "stop": stop / 100, "target": target / 100}))),
    ]


# ---- 有效变体：每一项都必须恰好压在界限上且一条发现都没有 ----


def _variants(values: dict[str, float]) -> list[tuple[str, Case]]:
    """六个有效变体。界限上的比较允许 1e-9 的浮点误差（蓝图 6.6），所以恰好压线也算通过。"""
    close, atr = values["close"], values["atr_14"]
    entry = close
    stop = entry - _STOP_ATR * atr
    base = _build_case(values)

    at_risk_limit = base.proposal.model_copy(update={         # 风险恰好在上限：仓位 = entry/(entry−stop)
        "stop": close - 2.5 * atr,
        "size_pct": close / (2.5 * atr),
        "target": close + 2.0 * (2.5 * atr),
    })
    stop_one_atr = base.proposal.model_copy(update={"stop": close - 1.0 * atr})   # 止损恰好 1.0 倍 ATR
    reward_risk_15 = base.proposal.model_copy(update={        # 盈亏比恰好 1.5
        "target": entry + 1.5 * (entry - stop),
    })
    entry_two_atr = base.proposal.model_copy(update={         # 入场价恰好在 2.0 倍 ATR 处，价位随之平移
        "entry": close + 2.0 * atr,
        "stop": close + 2.0 * atr - _STOP_ATR * atr,
        "target": close + 2.0 * atr + _TARGET_ATR * atr,
    })
    no_target = base.proposal.model_copy(update={"target": None})   # 没有目标价：盈亏比不适用
    rounded_claim = _with_claim(base, 2, value=round(values["rsi_14"], 2))   # claim 数值舍入到两位小数

    return [
        ("单笔风险恰好在上限", replace(base, proposal=at_risk_limit)),
        ("止损恰好 1.0 倍 ATR 之下", replace(base, proposal=stop_one_atr)),
        ("盈亏比恰好 1.5", replace(base, proposal=reward_risk_15)),
        ("入场价恰好偏离市价 2.0 倍 ATR", replace(base, proposal=entry_two_atr)),
        ("没有目标价", replace(base, proposal=no_target)),
        ("claim 的数值舍入到两位小数", rounded_claim),
    ]


def _checks(findings: list[Finding]) -> str:
    """发现里出现过的检查项，供漏报或误报时诊断。"""
    return "、".join(dict.fromkeys(f.check for f in findings))


def _load_values() -> tuple[list[Fact], dict[str, float], tuple[RuleSpec, ...]]:
    """真实账本：ACME 在 2026-09-30 的全部事实，加上从 data/rules.toml 读入的规则手册。"""
    indicator_specs = load_indicators(_ROOT / "data" / "indicators.toml")
    specs = load_rules(_ROOT / "data" / "rules.toml")
    market = CsvMarket(_ROOT / "data" / "market", "synthetic")
    bars = market.history(_TICKER, _AS_OF)
    facts, _card = build_ledger(market.instrument(_TICKER), bars, _AS_OF, indicator_specs,
                                market.dropped_rows(_TICKER))
    return facts, {f.id: f.value for f in facts}, specs


def main() -> int:
    """跑注入评测。全部按预期打印结论并退出 0；有漏报或误报打印明细并退出 1。"""
    facts, values, specs = _load_values()
    base = _build_case(values)
    baseline = _evaluate(base, facts, values, specs)
    print(f"注入评测：{values['close']:.2f} 为收盘价的事实账本，基线案例应无任何发现")
    if baseline:
        print(f"  基线案例不干净（{_checks(baseline)}）：先修案例再谈注入")
        return 1

    misses = 0
    print("故障注入（每一项都应被发现）")
    mutations = _mutations(values)
    for i, mutation in enumerate(mutations, start=1):
        findings = _evaluate(mutation.apply(base), facts, values, specs)
        found = any(f.check == mutation.check for f in findings)
        verdict = "发现" if found else f"漏报（实际触发：{_checks(findings) or '无'}）"
        misses += not found
        print(f"  {i:>2}  {mutation.label:<20} {mutation.check:<26} {verdict}")

    false_alarms = 0
    print("有效变体（每一项都应无发现）")
    variants = _variants(values)
    for i, (label, case) in enumerate(variants, start=1):
        findings = _evaluate(case, facts, values, specs)
        clean = not findings
        false_alarms += not clean
        verdict = "无发现" if clean else f"误报（{_checks(findings)}）"
        print(f"  {i:>2}  {label:<20} {'':<26} {verdict}")

    print(f"结论：故障 {len(mutations) - misses}/{len(mutations)} 被发现；"
          f"有效变体 {false_alarms}/{len(variants)} 误报")
    return 0 if misses == 0 and false_alarms == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
